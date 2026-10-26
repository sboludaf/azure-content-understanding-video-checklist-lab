targetScope = 'resourceGroup'

@description('Registry region selected and checked by the deployer. No automatic fallback.')
param location string = resourceGroup().location

@description('App Service Plan and Web App region. Select explicitly after capacity checks; ACR remains in location.')
param hostingLocation string = location

@minLength(2)
@maxLength(60)
@description('Exact, globally unique App Service name from the resource plan.')
param webAppName string

@minLength(1)
@maxLength(40)
@description('Exact App Service Plan name from the resource plan.')
param planName string

@allowed(['B1', 'B2'])
@description('Approved Linux Basic plan SKU. Select explicitly when reproducing a diagnostic deployment.')
param planSku string = 'B1'

@minLength(5)
@maxLength(50)
@description('Exact globally unique ACR name; alphanumeric only.')
param registryName string

@description('Owner public IPv4 CIDR /32 or IPv6 CIDR /128. Never use 0.0.0.0/0 or ::/0.')
@secure()
param ownerIpCidr string

@description('Immutable repository:tag, preferably the full source commit SHA. No registry hostname.')
param imageReference string

@description('Full source Git SHA matching Docker build ARG MVP_SOURCE_COMMIT.')
param expectedCommit string

@description('False creates ACR only; build the image before creating the hosting resources.')
param deployHosting bool = true

@allowed(['fixture', 'azure'])
@description('Start in fixture until managed identity, model and scoped authorization are verified.')
param analysisMode string = 'fixture'

@description('Existing or separately authorized AI resource ID; this template does not create AI resources or modify their defaults.')
param aiResourceId string = ''

@description('Exact approved cleanup time. The tag is metadata; no cron or automatic deletion is created.')
param cleanupDueAt string

@secure()
@description('Additional private MVP app settings. Supply via an untracked private parameters file; do not commit credentials or authorization payloads.')
param applicationSettings object = {}

var labId = '2026-10-15-content-understanding-video-checklist'
var proposalHash = '0f751b92278e7bb3994fcdfadf6761c100037f839bb1daf9641b5e7c12ee30b7'
var privateRoot = '/home/mvp-labs/${labId}'
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var ownerNetworkAllowed = endsWith(ownerIpCidr, '/32') || endsWith(ownerIpCidr, '/128')
var tags = {
  'lab-id': labId
  'proposal-hash': proposalHash
  purpose: 'private-video-checklist-lab'
  'cleanup-at': cleanupDueAt
  'app-onboard-skill': 'true'
}
var protectedSiteConfiguration = {
  acrUseManagedIdentityCreds: true
  alwaysOn: true
  numberOfWorkers: 1
  minTlsVersion: '1.2'
  scmMinTlsVersion: '1.2'
  http20Enabled: true
  ftpsState: 'Disabled'
  remoteDebuggingEnabled: false
  webSocketsEnabled: false
  healthCheckPath: '/health'
  ipSecurityRestrictionsDefaultAction: 'Deny'
  ipSecurityRestrictions: ownerNetworkAllowed ? [
    {
      name: 'owner-network'
      description: 'Only the owner network can reach this lab.'
      ipAddress: ownerIpCidr
      action: 'Allow'
      priority: 100
    }
  ] : []
  scmIpSecurityRestrictionsUseMain: true
  scmIpSecurityRestrictionsDefaultAction: 'Deny'
}

// API 2025-04-01 removed roleAssignmentMode. The deployer must verify/set
// RBAC Registry Permissions externally before this template assigns AcrPull.
resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource plan 'Microsoft.Web/serverfarms@2024-11-01' = if (deployHosting) {
  name: planName
  location: hostingLocation
  tags: tags
  kind: 'linux'
  sku: {
    name: planSku
    tier: 'Basic'
    capacity: 1
  }
  properties: {
    reserved: true
    perSiteScaling: false
  }
}

resource webApp 'Microsoft.Web/sites@2024-11-01' = if (deployHosting) {
  name: webAppName
  location: hostingLocation
  tags: tags
  kind: 'app,linux,container'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan!.id
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    clientAffinityEnabled: false
    siteConfig: protectedSiteConfiguration
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployHosting) {
  name: guid(registry.id, webApp!.id, acrPullRoleId)
  scope: registry
  properties: {
    principalId: webApp!.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
  }
}

// The child configuration can use the real hostname and system principal after
// creation, without a self-reference cycle in the sites resource.
resource appSettings 'Microsoft.Web/sites/config@2024-11-01' = if (deployHosting) {
  parent: webApp
  name: 'appsettings'
  properties: union(applicationSettings, {
    WEBSITES_PORT: '8000'
    WEBSITES_ENABLE_APP_SERVICE_STORAGE: 'true'
    WEBSITES_CONTAINER_START_TIME_LIMIT: '600'
    WEBSITE_WARMUP_PATH: '/health'
    WEBSITE_WARMUP_STATUSES: '200'
    DOCKER_REGISTRY_SERVER_URL: 'https://${registry.properties.loginServer}'
    MVP_HOSTED: 'true'
    MVP_BASE_URL: 'https://${webApp!.properties.defaultHostName}'
    MVP_ALLOWED_HOSTS: webApp!.properties.defaultHostName
    MVP_MODE: analysisMode
    MVP_ROLE: 'implementer'
    MVP_EXPECTED_COMMIT: expectedCommit
    MVP_AZURE_CREDENTIAL_MODE: 'managed_identity'
    MVP_AZURE_PRINCIPAL_ID: webApp!.identity.principalId
    MVP_AZURE_TENANT_ID: subscription().tenantId
    MVP_RUNTIME_DIR: '${privateRoot}/app'
    MVP_AUTHORIZATION_PATH: '${privateRoot}/authorization.json'
    MVP_LEDGER_PATH: '${privateRoot}/ledger.sqlite3'
    MVP_DEPLOYMENT_PATH: '${privateRoot}/deployment.json'
    MVP_SAMPLE_PATH: '${privateRoot}/media/base.mp4'
  })
  dependsOn: [
    acrPull
  ]
}

resource scmPublishing 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = if (deployHosting) {
  parent: webApp
  name: 'scm'
  properties: {
    allow: false
  }
}

resource ftpPublishing 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = if (deployHosting) {
  parent: webApp
  name: 'ftp'
  properties: {
    allow: false
  }
}

// Configure the image only after durable storage, hostname and identity settings
// exist. Initial image pull can still wait for RBAC propagation.
resource containerConfiguration 'Microsoft.Web/sites/config@2024-11-01' = if (deployHosting) {
  parent: webApp
  name: 'web'
  properties: union(protectedSiteConfiguration, {
    linuxFxVersion: 'DOCKER|${registry.properties.loginServer}/${imageReference}'
  })
  dependsOn: [
    appSettings
    scmPublishing
    ftpPublishing
  ]
}

output registryLoginServer string = registry.properties.loginServer
output registryResourceId string = registry.id
output webAppResourceId string = deployHosting ? webApp!.id : ''
output webAppHostName string = deployHosting ? webApp!.properties.defaultHostName : ''
output managedIdentityPrincipalId string = deployHosting ? webApp!.identity.principalId : ''
output appUrl string = deployHosting ? 'https://${webApp!.properties.defaultHostName}' : ''
output deploymentPlan object = {
  lab_id: labId
  proposal_hash: proposalHash
  status: deployHosting ? 'provisioned_pending_healthcheck' : 'registry_ready_for_build'
  resource_group_id: resourceGroup().id
  location: location
  commit: expectedCommit
  image: '${registry.properties.loginServer}/${imageReference}'
  analysis_mode: analysisMode
  ai_resource_id: aiResourceId
  runtime_root: privateRoot
  cleanup_due_at: cleanupDueAt
  hosting: {
    location: hostingLocation
    plan_resource_id: deployHosting ? plan!.id : ''
    plan_sku: planSku
    instances: 1
    app_resource_id: deployHosting ? webApp!.id : ''
    host_name: deployHosting ? webApp!.properties.defaultHostName : ''
    managed_identity_principal_id: deployHosting ? webApp!.identity.principalId : ''
    owner_ip_restriction_configured: ownerNetworkAllowed
    default_network_action: 'Deny'
  }
  registry: {
    location: location
    resource_id: registry.id
    login_server: registry.properties.loginServer
    sku: 'Basic'
    admin_user_enabled: false
    anonymous_pull_enabled: false
    acr_pull_role_assignment_id: deployHosting ? acrPull!.id : ''
  }
}
