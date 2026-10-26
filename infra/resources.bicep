targetScope = 'resourceGroup'

param location string
param hostingLocation string
param aiLocation string
@secure()
param allowedClientCidr string
param modelName string
param modelVersion string
param modelDeploymentName string
param modelDeploymentSku string
param modelCapacity int
param maxAnalyses int
param maxTotalMinutes int
param tags object

var resourceToken = uniqueString(resourceGroup().id)
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var openAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var singleClientAddress = endsWith(allowedClientCidr, '/32') || endsWith(allowedClientCidr, '/128')

// This stable API creates a registry using the standard registry RBAC model.
// AcrPull is scoped to this one registry; admin passwords and anonymous pull are disabled.
resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' = {
  name: 'crvcheck${resourceToken}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    roleAssignmentMode: 'LegacyRegistryPermissions'
    publicNetworkAccess: 'Enabled'
  }
}

resource aiAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: 'ai-vcheck-${resourceToken}'
  location: aiLocation
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: 'ai-vcheck-${resourceToken}'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: aiAccount
  name: modelDeploymentName
  sku: {
    name: modelDeploymentSku
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

resource plan 'Microsoft.Web/serverfarms@2024-11-01' = {
  name: 'asp-vcheck-${resourceToken}'
  location: hostingLocation
  tags: tags
  kind: 'linux'
  sku: {
    name: 'B1'
    tier: 'Basic'
    capacity: 1
  }
  properties: {
    reserved: true
    perSiteScaling: false
  }
}

resource webApp 'Microsoft.Web/sites@2024-11-01' = {
  name: 'app-vcheck-${resourceToken}'
  location: hostingLocation
  tags: union(tags, { 'azd-service-name': 'web' })
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    clientAffinityEnabled: false
    siteConfig: {
      // azd requires an existing DOCKER| configuration. This deliberately absent
      // image does not serve a demo; azd deploy replaces it with the real build.
      // Re-provisioning infrastructure must always be followed by azd deploy.
      linuxFxVersion: 'DOCKER|${registry.properties.loginServer}/video-checklist:pending-azd-deploy'
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
      // A malformed/broad CIDR cannot open the app: the default stays Deny.
      ipSecurityRestrictions: singleClientAddress ? [
        {
          name: 'deployer-address'
          description: 'Only the address configured by this deployment can use the lab.'
          ipAddress: allowedClientCidr
          action: 'Allow'
          priority: 100
        }
      ] : []
      scmIpSecurityRestrictionsUseMain: true
      scmIpSecurityRestrictionsDefaultAction: 'Deny'
    }
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, webApp.id, acrPullRoleId)
  scope: registry
  properties: {
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
  }
}

resource modelInference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiAccount.id, webApp.id, openAiUserRoleId)
  scope: aiAccount
  properties: {
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId)
  }
}

// App Service /home is persistent across container restarts. One instance and
// one ASGI worker own SQLite and operation recovery; this is not a scale-out design.
resource appSettings 'Microsoft.Web/sites/config@2024-11-01' = {
  parent: webApp
  name: 'appsettings'
  properties: {
    WEBSITES_PORT: '8000'
    WEBSITES_ENABLE_APP_SERVICE_STORAGE: 'true'
    WEBSITES_CONTAINER_START_TIME_LIMIT: '600'
    WEBSITE_WARMUP_PATH: '/health'
    WEBSITE_WARMUP_STATUSES: '200'
    DOCKER_REGISTRY_SERVER_URL: 'https://${registry.properties.loginServer}'
    MVP_HOSTED: 'true'
    MVP_RUNTIME_PROFILE: 'azd'
    MVP_MODE: 'azure'
    MVP_AZURE_PROVIDER: 'azure_foundry_responses'
    MVP_BASE_URL: 'https://${webApp.properties.defaultHostName}'
    MVP_ALLOWED_HOSTS: webApp.properties.defaultHostName
    MVP_AZURE_ENDPOINT: 'https://${aiAccount.name}.openai.azure.com'
    MVP_AZURE_DEPLOYMENT_NAME: modelDeployment.name
    MVP_AZURE_MODEL: modelName
    MVP_AZURE_MODEL_VERSION: modelVersion
    MVP_AZURE_REGION: aiLocation
    MVP_AZURE_CREDENTIAL_MODE: 'managed_identity'
    MVP_AZURE_PRINCIPAL_ID: webApp.identity.principalId
    MVP_AZURE_TENANT_ID: subscription().tenantId
    MVP_RUNTIME_DIR: '/home/video-checklist'
    MVP_MAX_ANALYSES: string(maxAnalyses)
    MVP_MAX_TOTAL_MINUTES: string(maxTotalMinutes)
  }
  dependsOn: [acrPull, modelInference]
}

resource scmPublishing 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: webApp
  name: 'scm'
  properties: { allow: false }
}

resource ftpPublishing 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: webApp
  name: 'ftp'
  properties: { allow: false }
}

output registryName string = registry.name
output registryEndpoint string = registry.properties.loginServer
output webAppName string = webApp.name
output appUrl string = 'https://${webApp.properties.defaultHostName}'
output aiAccountName string = aiAccount.name
output aiEndpoint string = 'https://${aiAccount.name}.openai.azure.com'
