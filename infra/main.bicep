targetScope = 'subscription'

@minLength(2)
@maxLength(64)
@description('Azure Developer CLI environment name. Resources are isolated in a new resource group.')
param environmentName string

@description('Resource-group metadata and container registry region selected by the deployer.')
param location string

@description('App Service region. Choose one with Linux Basic capacity in your subscription.')
param hostingLocation string = location

@description('Foundry account region. The chosen model/version/SKU must be available with quota here.')
param aiLocation string = location

@secure()
@minLength(7)
@maxLength(49)
@description('Your public IPv4 /32 or IPv6 /128. Only this address can use the app. There is no public demo.')
param allowedClientCidr string

@description('An image-capable Responses API model available in your own Azure subscription.')
param modelName string = 'gpt-6.1-sol'

@description('Exact Azure model version; no automatic upgrade to a different model version.')
param modelVersion string = '2026-09-29'

@description('Your Azure model deployment name, consumed by the application.')
param modelDeploymentName string = 'video-checklist'

@allowed(['GlobalStandard', 'DataZoneStandard', 'Standard'])
@description('Model deployment SKU supported for this model/version/region and your data residency needs.')
param modelDeploymentSku string = 'GlobalStandard'

@minValue(1)
@maxValue(100)
@description('Model capacity units. Units and available quota depend on the selected model/SKU.')
param modelCapacity int = 1

@minValue(1)
@maxValue(999999999)
@description('Local reservation limit for this deployment. This is not an Azure billing cap.')
param maxAnalyses int = 1000

@minValue(1)
@maxValue(999999999)
@description('Local video-minute reservation limit. This is not an Azure billing cap.')
param maxTotalMinutes int = 1500

var tags = {
  'azd-env-name': environmentName
  application: 'video-checklist'
}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-video-checklist-${environmentName}'
  location: location
  tags: tags
}

module resources './resources.bicep' = {
  name: 'video-checklist-resources'
  scope: resourceGroup
  params: {
    location: location
    hostingLocation: hostingLocation
    aiLocation: aiLocation
    allowedClientCidr: allowedClientCidr
    modelName: modelName
    modelVersion: modelVersion
    modelDeploymentName: modelDeploymentName
    modelDeploymentSku: modelDeploymentSku
    modelCapacity: modelCapacity
    maxAnalyses: maxAnalyses
    maxTotalMinutes: maxTotalMinutes
    tags: tags
  }
}

// azd persists these outputs in .azure/<environment>/.env. No secret keys are used.
output AZURE_RESOURCE_GROUP string = resourceGroup.name
output AZURE_CONTAINER_REGISTRY_NAME string = resources.outputs.registryName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryEndpoint
output AZURE_WEBAPP_NAME string = resources.outputs.webAppName
output AZURE_AI_ACCOUNT_NAME string = resources.outputs.aiAccountName
output AZURE_AI_ENDPOINT string = resources.outputs.aiEndpoint
output SERVICE_WEB_RESOURCE_NAME string = resources.outputs.webAppName
output SERVICE_WEB_ENDPOINT_URL string = resources.outputs.appUrl
output AZURE_OPENAI_DEPLOYMENT_NAME string = modelDeploymentName
output AZURE_OPENAI_MODEL_NAME string = modelName
output AZURE_OPENAI_MODEL_VERSION string = modelVersion
