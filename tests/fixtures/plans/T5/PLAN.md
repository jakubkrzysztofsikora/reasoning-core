# PLAN.md

## Stage 1

### Endpoint

`POST /api/admin/users/create` → `Circit.Website/Areas/Admin/Controllers/UsersAdminController.cs:43`,
method `Create(UserDataModel)`.

Attributes that enforce RBAC:
- `Circit.Website/Areas/Admin/Controllers/UsersAdminController.cs:18` `[Route("api/admin/users")]`
- `Circit.Website/Areas/Admin/Controllers/UsersAdminController.cs:20` `[Authorize(Policy = "IsCircit")]`
- `Circit.Website/Areas/Admin/Controllers/UsersAdminController.cs:40` `[HttpPost]`
- `Circit.Website/Areas/Admin/Controllers/UsersAdminController.cs:41` `[Authorize(Policy = "CanEditUsers")]`
- `Circit.Website/Areas/Admin/Controllers/UsersAdminController.cs:42` `[Route("create")]`

Policy registrations exercised by the test:
- `Circit.Website/App_Start/ConfigureAuthentication.cs:255` `Policies.IsCircit` ⇒ `RequireRole("CIRCIT")`
- `Circit.Website/App_Start/ConfigureAuthentication.cs:306-310` `Policies.CanEditUsers` ⇒
  `RequireRole("CIRCIT")` plus `RequireClaim(Claims.UserManagementAccess, CircitUserClaimValues.Edit)`

Constants used by both production and test:
- `Circit.Common/Constants/Claims.cs:7` `Policies.IsCircit`
- `Circit.Common/Constants/Claims.cs:23` `Policies.CanEditUsers`
- `Circit.Common/Constants/Claims.cs:90` `Claims.UserManagementAccess`
- `Circit.Common/Constants/CircitUserClaimValues.cs` `Edit`, `View`
- `Circit.Common/Enums/RoleType.cs:16` `RoleType.Circit`
- `Circit.Common/Extensions/EnumExtensions.cs:30` `EnumExtensions.ToUpper(Enum)`

Action body that the negative test must prove never runs:
- `Circit.Website/Areas/Admin/Controllers/UsersAdminController.cs:56` `userService.CreateUser(...)`
- `Circit.Website/Areas/Admin/Controllers/UsersAdminController.cs:63`
  `notificationManager.SendActivationEmail(userId)`

Successful response shape:
- `Circit.Website/Areas/Admin/Controllers/UsersAdminController.cs:72`
  `Ok(new { success = true, activationEmailSent })`

### Why the endpoint satisfies all four selection criteria

1. Privileged: dual `[Authorize]` attributes at lines 20 and 41, plus mandatory listing in
   `Tests/Circit.Website.Tests/Areas/Admin/AdminAuthorisationPolicyTests.cs:18-19`
   (`Policies.CanEditUsers`, `Policies.CanViewUsers`).
2. Mutating: `POST` writes a `User` row and dispatches an activation email.
3. Documented response: 200 payload at line 72; denial flows through ASP.NET Core
   `AuthorizationMiddlewareResultHandler` (401 anonymous / 403 authenticated).
4. Reachable in seeded environment: no feature-flag gate; the controller is part of the default
   Admin area built by `Circit.Website/Startup.cs`.

### RBAC mechanism in code

`Circit.Website/Startup.cs` calls `AddAppPolicies()` declared at
`Circit.Website/App_Start/ConfigureAuthentication.cs:239-247`. The middleware order in
`Circit.Website/Startup.cs` runs `app.UseAuthentication()` then `app.UseAuthorization()`. Failure
produces 401 or 403 via the framework's `AuthorizationMiddlewareResultHandler`.

### Test file path

`Tests/Circit.Website.Tests/Areas/Admin/UsersAdminCreateRbacTests.cs`.

### Framework

NUnit, Shouldly, Moq, Moq.AutoMock — already declared in
`Tests/Circit.Website.Tests/Circit.Website.Tests.csproj`. No new top-level dependency added.

### Test layers

#### Layer A — endpoint surface (reflection)

Load `Circit.Website.dll`, locate `UsersAdminController.Create`, assert:
- class type carries `AuthorizeAttribute` whose `Policy == Policies.IsCircit`;
- method carries `AuthorizeAttribute` whose `Policy == Policies.CanEditUsers`;
- method carries `HttpPostAttribute`;
- method carries `RouteAttribute` whose `Template == "create"`;
- class carries `RouteAttribute` whose `Template == "api/admin/users"`.

Removal of either `[Authorize]` attribute fails Layer A.

#### Layer B — policy semantics (real `AddAppPolicies` registration)

Build `ServiceCollection`, call production
`Circit.Website.App_Start.ConfigureAuthenticationExtensions.AddAppPolicies(...)`, resolve
`IAuthorizationService` plus `IAuthorizationPolicyProvider`, evaluate `Policies.CanEditUsers`
against six handcrafted `ClaimsPrincipal`s.

| Case | Identity | Role claim | UserManagementAccess claim | Expected `Succeeded` | HTTP analogue |
|------|----------|------------|----------------------------|----------------------|---------------|
| A — anonymous | unauthenticated | none | none | false | 401 |
| B — auditor only | authenticated `auditor@x.com` | `AUDITOR` | none | false | 403 |
| C — auditor + Edit claim | authenticated `auditor@x.com` | `AUDITOR` | `Edit` | false | 403 |
| D — Circit, no claim | authenticated `circit@x.com` | `CIRCIT` | none | false | 403 |
| E — Circit + View | authenticated `circit@x.com` | `CIRCIT` | `View` | false | 403 |
| F — Circit + Edit (positive control) | authenticated `circit@x.com` | `CIRCIT` | `Edit` | true | 200 |

The 401-versus-403 distinction is asserted via `principal.Identity?.IsAuthenticated`, mirroring
`AuthorizationMiddlewareResultHandler`.

#### Side-effect-absence assertions

Stores observed: `IAdminUserService` (writes the new `User` row at
`UsersAdminController.cs:56`) plus `INotificationService` (sends the activation email at
`UsersAdminController.cs:63`). Both are wired as `Mock<>` instances. After every denied case the
test asserts:
- `Mock<IAdminUserService>.Verify(s => s.CreateUser(It.IsAny<UserDto>()), Times.Never)`
- `Mock<INotificationService>.Verify(n => n.SendActivationEmail(It.IsAny<int>(), ...),
  Times.Never)`

Observation window: synchronous, in-process, single thread. No queue, DB transaction, or
background job sits between policy evaluation and the asserted absence — "no call observed" is
positive proof, not absence-of-evidence within a timeout.

For positive-control case F: assert `CreateUser` called exactly once with a DTO whose `Email`
matches the request, the action returns `OkObjectResult`, the payload's `success` field is
`true`.

#### Audit-log note

The Circit pipeline does not write a denial row when the authorization middleware short-circuits
a request — denials surface via `Circit.Website/Infrastructure/Middleware/SentryMiddleware.cs`
plus `Circit.Website/Infrastructure/Middleware/AdvancedTelemetryMiddleware.cs`, not via a row in
the `AuditItemHistories` table. The test does not assert a denial row that the production code
does not write.

### User seeding and tear-down

No DB seeding. `ClaimsPrincipal` instances are constructed in-memory inside each test and
discarded when the method returns. Mocks are recreated per test by NUnit `[SetUp]`. Nothing to
tear down.

### Mutation-test reasoning

Four removals each caught by at least one assertion:
1. Delete `[Authorize(Policy = "CanEditUsers")]` from `UsersAdminController.Create`
   (`UsersAdminController.cs:41`) → Layer A method-attribute assertion fails.
2. Delete `[Authorize(Policy = "IsCircit")]` from `UsersAdminController` class
   (`UsersAdminController.cs:20`) → Layer A class-attribute assertion fails.
3. Delete `RequireRole(RoleType.Circit.ToUpper())` at
   `ConfigureAuthentication.cs:308` → Layer B case C ("auditor with Edit claim") flips Failed →
   Succeeded → fails.
4. Delete `RequireClaim(Claims.UserManagementAccess, CircitUserClaimValues.Edit)` at
   `ConfigureAuthentication.cs:309` → Layer B case D ("Circit role, no claim") flips Failed →
   Succeeded → fails.

### Risks (named, non-generic)

1. Empty 200 false-pass — Layer B case F asserts both `Succeeded == true` plus the response payload
   field `success == true` (not just `result is OkObjectResult`).
2. Stale `ClaimsPrincipal` cache — N/A: every test builds a fresh principal; no auth provider is
   instantiated.
3. Stale token — N/A: no real auth tokens are issued; the test does not go through cookie
   middleware.
4. Delayed downstream side effect — mitigated by `Times.Never` on the synchronous controller
   invocation; no queue, DB transaction, or background job exists between the policy decision plus
   the assertion.
5. Audit-log eventual consistency — N/A; documented above (Circit logs denials only via Sentry plus
   AppInsights middleware, not a DB row).
6. Casing drift on the role string — production plus test both reference
   `Circit.Common/Extensions/EnumExtensions.cs:30` `Enum.ToUpper()`. A casing rename breaks them
   together; cases A plus B catch any change that drops the role check entirely.
7. Renamed policy key — Layer B asks
   `IAuthorizationPolicyProvider.GetPolicyAsync(Policies.CanEditUsers)`; if the registration
   string at `ConfigureAuthentication.cs:306` diverges from the constant at `Claims.cs:23`, the
   provider returns null and the test fails with `ShouldNotBeNull()`.
8. `InternalApiExceptionAttribute`
   (`Circit.Website/Infrastructure/Filters/InternalApiExceptionAttribute.cs`) masking 401 → 200 —
   N/A: that filter intercepts exceptions thrown by the action, not authorization-middleware
   denials.

### How to run locally

```
dotnet test Tests/Circit.Website.Tests/Circit.Website.Tests.csproj \
  --filter "FullyQualifiedName~UsersAdminCreateRbacTests" \
  --logger "console;verbosity=minimal"
```

Expected exit code: `0`. Eight test methods in the new fixture pass (six policy cases plus class
attribute reflection plus method attribute reflection plus positive-control invocation plus
denied-invocation side-effect proof, organised via `[TestCase]` rows).

## Stage 2

Implementation is `Tests/Circit.Website.Tests/Areas/Admin/UsersAdminCreateRbacTests.cs` plus the
harness artefacts (`Tests/locked.jsonl`, `Tests/rotated.jsonl`, `tokens.json`, `safety.json`,
`DIVERGENCES.md`).
