// Full environment for one agent service.
//
//   Container Apps (the agent) ── Managed Identity ──┬── Azure OpenAI
//                │                                   ├── Cosmos DB (memory)
//                │                                   └── Key Vault (Langfuse keys)
//                └── Log Analytics + Application Insights
//
// No secret is ever passed as a plain environment variable: the app
// authenticates to every Azure dependency with its user-assigned identity, and
// third-party keys (Langfuse) are mounted as Container Apps secrets sourced
// from Key Vault.
//
//   az deployment group create -g <rg> -f main.bicep -p @params.dev.json

targetScope = 'resourceGroup'

@description('Short name for the workload, 3-12 lowercase chars.')
@minLength(3)
@maxLength(12)
param appName string = 'agentkit'

@allowed(['dev', 'test', 'prod'])
param environmentName string = 'dev'

param location string = resourceGroup().location

@description('Container image, e.g. myacr.azurecr.io/agent:1.0.0')
param containerImage string

@description('Azure OpenAI model deployment.')
param openAiModel string = 'gpt-4o-mini'
param openAiModelVersion string = '2024-07-18'
param openAiCapacity int = 30

@description('Scale bounds. minReplicas 0 gives scale-to-zero; use 1+ in prod to avoid cold starts.')
param minReplicas int = environmentName == 'prod' ? 2 : 0
param maxReplicas int = environmentName == 'prod' ? 10 : 3

@description('Langfuse endpoint; keys are read from Key Vault, never from params.')
param langfuseHost string = 'https://cloud.langfuse.com'

var suffix = uniqueString(resourceGroup().id, appName, environmentName)
var tags = {
  workload: appName
  environment: environmentName
  managedBy: 'bicep'
}

// ---------------------------------------------------------------- identity

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${appName}-${environmentName}'
  location: location
  tags: tags
}

// ------------------------------------------------------------ observability

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${appName}-${environmentName}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: environmentName == 'prod' ? 90 : 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${appName}-${environmentName}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

// -------------------------------------------------------------- key vault

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-${appName}-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: environmentName == 'prod' ? 'Disabled' : 'Enabled'
  }
}

// Key Vault Secrets User
resource kvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identity.id, '4633458b')
  scope: keyVault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6'
    )
  }
}

// ------------------------------------------------------------ azure openai

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'oai-${appName}-${suffix}'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: 'oai-${appName}-${suffix}'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true // force Entra ID auth; no API keys to leak
  }
}

resource openAiDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAi
  name: openAiModel
  sku: { name: 'Standard', capacity: openAiCapacity }
  properties: {
    model: { format: 'OpenAI', name: openAiModel, version: openAiModelVersion }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
}

// Cognitive Services OpenAI User
resource openAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAi.id, identity.id, '5e0bd9bd')
  scope: openAi
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
    )
  }
}

// ---------------------------------------------------------------- cosmos db

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: 'cosmos-${appName}-${suffix}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [{ locationName: location, failoverPriority: 0, isZoneRedundant: false }]
    capabilities: environmentName == 'prod' ? [] : [{ name: 'EnableServerless' }]
    disableLocalAuth: true
    backupPolicy: { type: 'Continuous', continuousModeProperties: { tier: 'Continuous7Days' } }
  }
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: 'agentmem'
  properties: { resource: { id: 'agentmem' } }
}

resource cosmosContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDb
  name: 'threads'
  properties: {
    resource: {
      id: 'threads'
      partitionKey: { paths: ['/pk'], kind: 'Hash' }
      // Conversation turns expire after 30 days; long-term records set ttl: -1.
      defaultTtl: 2592000
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [{ path: '/pk/?' }, { path: '/doc_type/?' }, { path: '/ts/?' }]
        excludedPaths: [{ path: '/*' }]
      }
    }
  }
}

// Cosmos DB Built-in Data Contributor (data-plane RBAC, not ARM RBAC)
resource cosmosDataRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(cosmos.id, identity.id, 'data-contributor')
  properties: {
    principalId: identity.properties.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    scope: cosmos.id
  }
}

// ----------------------------------------------------------- container apps

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: 'acr${appName}${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { adminUserEnabled: false }
}

// AcrPull
resource acrRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, '7f951dda')
  scope: acr
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}

resource caEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${appName}-${environmentName}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    zoneRedundant: environmentName == 'prod'
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${appName}-${environmentName}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: caEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        // Blue/green: point 100% at the new revision only after smoke tests.
        traffic: [{ latestRevision: true, weight: 100 }]
        corsPolicy: { allowedOrigins: ['*'], allowedMethods: ['GET', 'POST'] }
      }
      registries: [{ server: acr.properties.loginServer, identity: identity.id }]
      secrets: [
        {
          name: 'langfuse-public-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/langfuse-public-key'
          identity: identity.id
        }
        {
          name: 'langfuse-secret-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/langfuse-secret-key'
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: appName
          image: containerImage
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: [
            { name: 'APP_ENV', value: environmentName }
            { name: 'LLM_PROVIDER', value: 'azure_openai' }
            { name: 'LLM_MODEL', value: openAiModel }
            { name: 'AZURE_OPENAI_ENDPOINT', value: openAi.properties.endpoint }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: openAiDeployment.name }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'MEMORY_BACKEND', value: 'cosmos' }
            { name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
            { name: 'COSMOS_DATABASE', value: cosmosDb.name }
            { name: 'COSMOS_CONTAINER', value: cosmosContainer.name }
            { name: 'OBSERVABILITY_PROVIDER', value: 'langfuse' }
            { name: 'LANGFUSE_HOST', value: langfuseHost }
            { name: 'LANGFUSE_PUBLIC_KEY', secretRef: 'langfuse-public-key' }
            { name: 'LANGFUSE_SECRET_KEY', secretRef: 'langfuse-secret-key' }
            { name: 'GUARDRAILS_POLICY', value: 'config/guardrails.yaml' }
            { name: 'GUARDRAILS_FAIL_MODE', value: 'block' }
            { name: 'AGENT_MAX_ITERATIONS', value: '8' }
            { name: 'AGENT_WALL_CLOCK_SECONDS', value: '120' }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: { path: '/readyz', port: 8000 }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: { metadata: { concurrentRequests: '20' } }
          }
        ]
      }
    }
  }
  dependsOn: [acrRole, openAiRole, cosmosDataRole, kvRole]
}

output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
output acrLoginServer string = acr.properties.loginServer
output identityClientId string = identity.properties.clientId
output openAiEndpoint string = openAi.properties.endpoint
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output keyVaultName string = keyVault.name
