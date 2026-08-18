param(
    [Parameter(Mandatory = $true)]
    [string]$AcrName,

    [string]$ResourceGroup = "rg-marketbrief",
    [string]$EnvironmentName = "marketbrief-env",
    [string]$AppName = "marketbrief-mcp",
    [string]$Location = "uksouth",
    [string]$ImageName = "marketbrief-mcp",
    [string]$Tag = "latest",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

Write-Host "Checking Azure login..."
az account show --output none 2>$null
if ($LASTEXITCODE -ne 0) {
    az login | Out-Null
}

Write-Host "Ensuring required providers are registered..."
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.ContainerRegistry --wait

Write-Host "Ensuring resource group '$ResourceGroup' exists..."
$rgExists = az group exists --name $ResourceGroup --output tsv
if ($rgExists -ne "true") {
    az group create --name $ResourceGroup --location $Location --output none
}

$loginServer = az acr show --name $AcrName --query loginServer --output tsv
if (-not $loginServer) {
    throw "Could not resolve ACR '$AcrName'."
}

$image = "$loginServer/$ImageName`:$Tag"

if (-not $SkipBuild) {
    Write-Host "Building and pushing $image via ACR Tasks..."
    az acr build `
        --registry $AcrName `
        --image "$ImageName`:$Tag" `
        .
}

Write-Host "Ensuring Container Apps environment '$EnvironmentName' exists..."
$envId = az containerapp env show `
    --name $EnvironmentName `
    --resource-group $ResourceGroup `
    --query id `
    --output tsv 2>$null

if (-not $envId) {
    az containerapp env create `
        --name $EnvironmentName `
        --resource-group $ResourceGroup `
        --location $Location `
        --output none
}

# Use ACR credentials for the initial deployment. This keeps the deployment
# script compatible with an existing ACR. We can switch to managed identity
# later without changing the container image or MCP endpoint.
$acrUser = az acr credential show --name $AcrName --query username --output tsv
$acrPassword = az acr credential show --name $AcrName --query "passwords[0].value" --output tsv
if (-not $acrUser -or -not $acrPassword) {
    Write-Host "ACR admin credentials are unavailable; enabling the admin account..."
    az acr update --name $AcrName --admin-enabled true --output none
    $acrUser = az acr credential show --name $AcrName --query username --output tsv
    $acrPassword = az acr credential show --name $AcrName --query "passwords[0].value" --output tsv
}

$appExists = az containerapp show `
    --name $AppName `
    --resource-group $ResourceGroup `
    --query name `
    --output tsv 2>$null

if ($appExists) {
    Write-Host "Updating existing Container App '$AppName'..."
    az containerapp registry set `
        --name $AppName `
        --resource-group $ResourceGroup `
        --server $loginServer `
        --username $acrUser `
        --password $acrPassword `
        --output none

    az containerapp update `
        --name $AppName `
        --resource-group $ResourceGroup `
        --image $image `
        --min-replicas 0 `
        --max-replicas 1 `
        --cpu 0.5 `
        --memory 1.0Gi `
        --output none

    az containerapp ingress enable `
        --name $AppName `
        --resource-group $ResourceGroup `
        --type external `
        --target-port 8080 `
        --transport auto `
        --output none
}
else {
    Write-Host "Creating Container App '$AppName'..."
    az containerapp create `
        --name $AppName `
        --resource-group $ResourceGroup `
        --environment $EnvironmentName `
        --image $image `
        --ingress external `
        --target-port 8080 `
        --transport auto `
        --registry-server $loginServer `
        --registry-username $acrUser `
        --registry-password $acrPassword `
        --min-replicas 0 `
        --max-replicas 1 `
        --cpu 0.5 `
        --memory 1.0Gi `
        --output none
}

$fqdn = az containerapp show `
    --name $AppName `
    --resource-group $ResourceGroup `
    --query properties.configuration.ingress.fqdn `
    --output tsv

Write-Host ""
Write-Host "Deployment complete."
Write-Host "Health: https://$fqdn/health"
Write-Host "MCP:    https://$fqdn/mcp"
Write-Host ""
Write-Host "Test health with:"
Write-Host "Invoke-RestMethod https://$fqdn/health"
Write-Host ""
Write-Host "Test MCP with:"
Write-Host "`$env:MARKETBRIEF_MCP_URL='https://$fqdn/mcp'; py .\Test-MarketBriefMcp.py"
