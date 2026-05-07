# PLAN.md — Task T8

## Stage 1 — Discovery

### Goal restated
Rename discriminator on banking authorisation events distinguishing provider categories (BDA vs non-BDA, or equivalent provider-kind concept) to `providerCategory` / `ProviderCategory` / `provider_category`.

### Discovery report (file:line)

#### Candidate A — existing enum already named `ProviderCategory`
- `Circit.Common/Enums/ProviderCategory.cs:3` — enum `{ Bank=1, Legal=2, Other=3, FundServices=4, Depositary=5, TransferAgent=6, Custodian=7, AssetManager=8, AircraftLessors=9, Fund=10, AIFM=11, FundAdministrator=12, Company=13, Promoter=14, ArAp=15 }`.
- `Circit.Data/Model/ProviderCompany.cs:77` — `public ProviderCategory ProviderCategory { get; set; }`.
- `Circit.Data/Model/SingleUseProviderCompany.cs:22` — assignment from DTO.
- `Circit.Data/Model/SingleUseProviderCompany.cs:39` — second assignment site.
- `Circit.Data/Model/Dto/SingleUseProviderDto.cs:12` — `public ProviderCategory? Category { get; init; }`.
- `Circit.Data/Model/Dto/SingleUseProviderDto.cs:30` — null-coalesce to `Other`.
- `Circit.Website/Extensions/ProviderCompanySelectItem.cs:16` — projection.
- `Circit.Common/Resources/Words.resx:2401` — i18n key.
- `Circit.Common/Resources/Words.de.resx:2469`, `Words.es.resx:2476`, `Words.fr.resx:2478`, `Words.nl-BE.resx:2455`, `Words.pl.resx:1292`, `Words.pt.resx:2311` — 6 locale resx variants.
- `Circit.Common/Resources/Words.Designer.cs:6017` — generated accessor.
- EF migration designer snapshots referencing column `ProviderCategory` on `ProviderCompany`:
  - `Circit.Data/Migrations/20250901000000_InitialBaseline.cs:1322`
  - `Circit.Data/Migrations/20250901000000_InitialBaseline.Designer.cs:5309`
  - `Circit.Data/Migrations/20250901142236_AddUpdatedByAndUpdatedOnToDocument.Designer.cs:5305`
  - `Circit.Data/Migrations/20250914100058_RemoveNciIndexesAndUpdateMappings.Designer.cs:5394`
  - `Circit.Data/Migrations/20251015140908_AddAuditItemExceptionReasonTable.Designer.cs:5494`
  - `Circit.Data/Migrations/20251028154635_AddPbcDocumentWopiHistory.Designer.cs:5537`
  - `Circit.Data/Migrations/20251029113751_AddReviewStatusToPbcDocument.Designer.cs:5513`
  - `Circit.Data/Migrations/20251107151940_AddResponsesEmailTables.Designer.cs:5609`
  - `Circit.Data/Migrations/20251211110606_AddCustomConfirmationsFileDownloadSettings.Designer.cs:5771`
  - `Circit.Data/Migrations/20260113180148_DropProviderPortalRequestsView.Designer.cs:5743`
  - `Circit.Data/Migrations/20260129121322_AddInternalHealthStatusComment.Designer.cs:5779`
  - `Circit.Data/Migrations/20260203123823_AddAccountingDataFeature.Designer.cs:5914`
  - `Circit.Data/Migrations/20260204113101_AddAccountingDataRequestHistoryAndRename.Designer.cs:5954`
  - `Circit.Data/Migrations/20260213170024_DropAuditCompanyEmailTemplateHistory.Designer.cs:5923`
  - `Circit.Data/Migrations/20260221101435_RemovePendingChangesMaxLengthFromProviderCompany.Designer.cs:6069`
  - `Circit.Data/Migrations/20260225093545_AddAccountingDataRequestHistoryCreatedByUser.Designer.cs:6079`
  - `Circit.Data/Migrations/20260225132345_MadeJobIdOfCCGuid.Designer.cs:6078`
  - `Circit.Data/Migrations/20260309112925_AddSelectedConnectorToAccountingDataRequest.Designer.cs:6157`
  - `Circit.Data/Migrations/20260325190655_AddCrViewOnlyFeatures.Designer.cs:6193`
  - `Circit.Data/Migrations/20260403113249_EnrichAccountingDataRequest.Designer.cs:6201`
  - `Circit.Data/Migrations/20260408141801_AddIndexToBankAccountLog.Designer.cs:6204`
  - `Circit.Data/Migrations/20260413163833_MoveIsDeletedToCollaboratorBase.Designer.cs:6204`
  - `Circit.Data/Migrations/20260416161348_FixCollaboratorIndexes.Designer.cs:6207`
  - `Circit.Data/Migrations/20260422110115_AddCachedExternalEntityAndEngagementTables.Designer.cs:6368`

Semantics: classifies the *provider company* (Bank/Legal/Custodian/etc.). Already named `ProviderCategory`. Not a BDA-vs-non-BDA discriminator on auth events.

#### Candidate B — enum `ProviderType` (would collide on rename)
- `Circit.Common/Enums/ProviderType.cs:3` — `{ Verified=1, Integrated=2, Custom=3, SingleUse=4 }`.
- Used by `Circit.Common/Dtos/AuthorisationResponseDto.cs`, `Circit.Common/Dtos/ProviderCompanyDto.cs`, `Circit.Common/Dtos/AuditItemDto.cs`, `Circit.Common/Dtos/ITemplateInfo.cs`.
- Helpers: `Circit.Common/Helpers/ProviderHelper.cs`.
- Mappers: `Circit.Website/Mapping/AuditItemMapper.cs`, `Circit.Website/Mapping/ProviderCompanyMapper.cs`, `Circit.Website/Mapping/AuditHistory/Auditor/ConfirmationRequest/MapConfirmationRequestItemHistory.cs`, `Circit.Website/Mapping/AuditHistory/Auditor/ConfirmationRequest/MapConfirmationRequestResponseItemHistory.cs`.
- Open Banking auth handlers: `Circit.Website/Auth/OpenBanking/PermanentTsbCallbackHandler.cs`, `Circit.Website/Auth/OpenBanking/BaseBankCallbackHandler.cs`, `Circit.Website/Auth/OpenBanking/PlaidCallbackHandler.cs`, `Circit.Website/Auth/OpenBanking/CdrJwtTokenResponseValidationHandler.cs`, `Circit.Website/Auth/OpenBanking/BankResponseValidatorFactory.cs`.

Semantics: kind of provider integration. Renaming to `ProviderCategory` collides with Candidate A.

#### Candidate C — enum `ApiProviderType` (concrete provider implementation)
- `Circit.Common/Enums/ApiProviderType.cs` — flat enum of every concrete bank/provider.
- 775 references repo-wide (verified via `grep -c`).
- Grouping HashSets at `Circit.Common/Constants/Constants.cs:568` (`PremiumApiProviders`), `:575` (`SwiftApiProviders`), `:580` (`ImmediateAuthProviders`).
- `Circit.Data/Model/Authorisation.cs:121-122` references `ApiProvider.ApiProviderType is ApiProviderType.AbnAmroBai or ApiProviderType.AbnAmroBaiSandbox` to drive `CanSkipAuthorisation`.

Semantics: concrete provider, not a category. Rename would violate "no silent semantic change."

#### Candidate D — actual BDA-vs-non-BDA distinction (derived, not stored)
- `Circit.Services/Helpers/OpenBanking/ApiProviderConfigurationHelper.cs:7` — `public static bool IsSourceBankingDataApp(string configuration, string productName)` parses JSON `vtSource`/`rtSource` keys.
- `Circit.Services/Services/AdminServices/VerifiedTransactionAdminDocumentService.cs:184` — call site passing `bankAccount.Authorisation.ApiProvider.Configuration` and `"vtSource"`.
- `Circit.Website/Areas/ExternalApi/Controllers/BankingDataApp/BankingDataAppController.cs` — BDA endpoint surface.
- `Circit.Services/Services/ClientServices/BankingDataAppInboundService.cs` — BDA inbound flow.
- `Circit.Services/Services/ClientServices/AuthorisationService.cs:222`, `:418`, `:535`, `:555`, `:1020` — `BuildBankingDataAppAuthRequest` flow.
- `Circit.Services/Dtos/BankingDataApp/*.cs` (15 DTO files) — BDA-only DTOs identified by namespace, no in-payload discriminator.

The BDA distinction is encoded as parsed JSON inside `ApiProvider.Configuration` (column `nvarchar`), not as a scalar field on auth events.

#### Auth-event DTOs and transport contracts (verified absence of discriminator)
- `Circit.Common/Dtos/AuthorisationResponseDto.cs` — carries `ProviderType` (Candidate B) only.
- `Circit.Common/Dtos/AuthorisationHistoriesDto.cs` — no provider-kind field.
- `Circit.Services/Dtos/BankingDataApp/AuthPipelineCompletionParams.cs` — BDA-namespaced; no discriminator inside.
- `Circit.Services/Dtos/BankingDataApp/BankingDataAppWebhookParams.cs` — no discriminator.
- `Circit.Services/Dtos/BankingDataApp/OpenBankingAuthorisationResponse.cs` — no discriminator.
- OpenAPI specs at `Circit.Website/App_Data/openapi/{auditor-v1.yaml, auditor-v2.yaml, auditor-v3.yaml, provider-v1.yaml, provider-v2.yaml, provider-v3.yaml, internal-admin.yaml, internal-auditor.yaml, internal-clientsigner.yaml, internal-provider.yaml, internal-developer.yaml, internal-other.yaml}` — no `providerCategory` key on auth-event payloads.
- Frontend generated SDKs at `Circit.Frontend/src/{admin,auditor,client,provider,developer,external-auditor,external-provider}/api/generated/` — no field of this name.
- No event-bus / queue contracts: Circit uses Hangfire + HTTP webhooks (verified via lack of MassTransit/NServiceBus/Rebus packages in `Circit.Common/Circit.Common.csproj`, `Circit.Services/Circit.Services.csproj`, `Circit.Background/Circit.Background.csproj`).

### Case-convention mapping per workload

| Workload | Convention | Old | New |
|---|---|---|---|
| C# (Circit.Common, Circit.Data, Circit.Services, Circit.Website, Circit.Background, Circit.OpenBanking) | PascalCase | n/a | `ProviderCategory` |
| EF SQL columns | PascalCase | n/a | `ProviderCategory` |
| Vue 3 / TypeScript (Circit.Frontend) | camelCase | n/a | `providerCategory` |
| OpenAPI YAML (Circit.Website/App_Data/openapi) | camelCase JSON keys | n/a | `providerCategory` |
| Python | snake_case | n/a (no Python workload) | `provider_category` |

### Compatibility decision: clean cutover with **zero rename targets**

Three rename strategies were considered:

1. Rename `ApiProviderType` → `ApiProviderCategory`. Touches 775 references plus 23 EF migration designer snapshots, every OpenAPI spec, and every JSON test fixture under `Tests/Circit.OpenBanking.Tests/Data/` and `Tests/Circit.Services.Tests/Data/`. SQL column rename on `ApiProvider.ApiProviderType` requires a new EF migration and breaks the on-the-wire contract for external consumers of `auditor-v1.yaml`, `auditor-v2.yaml`, `auditor-v3.yaml`, `provider-v1.yaml`, `provider-v2.yaml`, `provider-v3.yaml`. Violates "no silent semantic change" — `ApiProviderType` values (AbnAmroBai, JpMorgan2Legged, GenericSwift) are not categories.

2. Rename `ProviderType` → `ProviderCategory`. Direct collision with the existing `Circit.Common/Enums/ProviderCategory.cs` and `Circit.Data/Model/ProviderCompany.cs:77` `ProviderCategory ProviderCategory` property. Resolution requires renaming the existing `ProviderCategory` first — that is restructuring, not a rename, and triggers the full Candidate-A blast radius (24 EF designer files, 7 resx variants, `Words.Designer.cs` regen, `SingleUseProviderDto.Category` rename).

3. Promote `IsSourceBankingDataApp` → stored boolean on `Authorisation` or `ApiProvider`. This is *adding a field*; spec forbids new fields under "rename".

**Decision: cutover is no-op.** Pre-condition for a meaningful rename — the existence of a single scalar discriminator with the described semantics on banking authorisation events — is not satisfied in this codebase. The closest matches are: (A) an enum already named `ProviderCategory` with unrelated semantics, (B) `ProviderType` whose rename collides with A, (C) `ApiProviderType` whose semantics are concrete-provider-instance and not category, (D) a parsed JSON value `IsSourceBankingDataApp` derived from `ApiProvider.Configuration`. Any rename violates one of: "do not silently change semantics," "no drive-by refactors," "complete rename with zero references to the old name."

### Migration order

Not applicable — no rename performed.

### Test additions/updates

Not applicable — no production code change. Verification is by re-running existing suites.

### Risk list

1. **Missed reference in EF migration designer files.** 24 designer snapshots at `Circit.Data/Migrations/*.Designer.cs` contain the literal `ProviderCategory`. A rename of Candidate A would require updating each. Avoided by no-op.
2. **JSON serializer attribute mismatch.** Generated TypeScript types at `Circit.Frontend/src/{admin,auditor,client,provider,developer,external-auditor,external-provider}/api/generated/` are produced by `npm run generate:api`; backend rename without regeneration silently desyncs the SPA at runtime. Avoided.
3. **Hangfire job version skew.** `Circit.Background` jobs serialize `ApiProviderType` ints into Hangfire SQL job arguments; renaming the C# enum changes the type FQN stored in `[HangFire].[Job]`, breaking in-flight jobs at the deploy boundary. Avoided.
4. **Persistence migration on existing rows.** `ProviderCategory` is `int NOT NULL` on `ProviderCompany`; SQL column rename requires `EF migrations add` and downtime-ordered deploy via `pipelines/main/core-pull-request.yml` `BicepValidation` + `BackendTests`. Avoided.
5. **Log enrichment dropping the field.** Application Insights custom dimensions and Sentry breadcrumbs reference `ApiProviderType` by literal name; KQL queries used by `support:log-investigation` skill at `support/skills/log-investigation` would orphan after rename. Avoided.
6. **i18n resx key drift.** `Circit.Common/Resources/Words.resx:2401` plus six locale variants (`Words.de.resx:2469`, `Words.es.resx:2476`, `Words.fr.resx:2478`, `Words.nl-BE.resx:2455`, `Words.pl.resx:1292`, `Words.pt.resx:2311`) hold `ProviderCategory` keys; `Words.Designer.cs:6017` generated accessor must be regenerated on rename. Avoided.
7. **External-API breaking change.** Specs at `Circit.Website/App_Data/openapi/{auditor-v1,auditor-v2,auditor-v3,provider-v1,provider-v2,provider-v3}.yaml` are versioned; any in-payload key rename forces a `v4` revision. Avoided.
8. **TPH discriminator collision.** `Circit.Data/Model/AuditItemBase.cs` uses `AuditItemType` as TPH discriminator with values `AuditItem`, `SigningDocument`, `ConfirmationRequest`, `ArApRequest`; an enum rename close in name space risks confusing future maintainers. Avoided.

### Verification plan

- `dotnet build Circit.sln` baseline.
- `dotnet test Tests/Circit.Common.Tests/Circit.Common.Tests.csproj` smoke run — Common tests are the most cross-cutting and quickest signal.
- `grep -rn "providerCategory\|ProviderCategory" Circit.Common Circit.Data Circit.Services Circit.Website Circit.Frontend Circit.Background Circit.OpenBanking | wc -l` — must equal pre-change baseline (no new refs, no removals).
- `npm --prefix Circit.Frontend run type-check` baseline.

These suites suffice to catch a missed reference because the change set is empty — any drift indicates an external factor, not a rename slip.

---

## Stage 2 — Implementation

No production-code edits. Artifacts produced:

- `PLAN.md` (this file).
- `DIVERGENCES.md`.
- `Tests/locked.jsonl` — 10 locked-seed run records.
- `Tests/rotated.jsonl` — 10 rotated-seed run records.
- `tokens.json`, `safety.json`, `transcript.jsonl`, `tool_calls.jsonl`.

The pre-existing `STARTED_WORK_2026-05-07T00:36:55Z` marker is left untouched.

### Final repo-wide reference check

References to `ProviderCategory` and `providerCategory` are the pre-existing Candidate-A enum and its uses; baseline preserved.
