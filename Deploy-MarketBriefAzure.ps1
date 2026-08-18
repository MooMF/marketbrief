param(
    [string]$AcrName = "ca799bf66e13acr",
    [string]$ResourceGroup = "ever-jobs-rg",
    [string]$EnvironmentName = "ever-jobs-env",
    [string]$AppName = "marketbrief-mcp",
    [string]$ImageName = "marketbrief-mcp",
    [string]$Tag = "latest",
    [switch]$SkipBuild,
    [switch]$SkipPush
)

$ErrorActionPreference = "Stop"

Write-Host "Checking Azure login..."
az account show --output none 2>$null
if ($LASTEXITCODE -ne 0) {
    az login | Out-Null
}

$loginServer = az acr show --name $AcrName --query loginServer --output tsv
if (-not $loginServer) {
    throw "Could not resolve ACR '$AcrName'."
}

$envId = az containerapp env show `
    --name $EnvironmentName `
    --resource-group $ResourceGroup `
    --query id `
    --output tsv 2>$null
if (-not $envId) {
    throw "Container Apps environment '$EnvironmentName' was not found in '$ResourceGroup'."
}

$image = "$loginServer/$ImageName`:$Tag"

if (-not $SkipBuild) {
    Write-Host "Building local Docker image marketbrief-mcp:$Tag..."
    docker build --no-cache -t "$ImageName`:$Tag" .
    if ($LASTEXITCODE -ne 0) { throw "Docker build failed." }

    docker tag "$ImageName`:$Tag" $image
    if ($LASTEXITCODE -ne 0) { throw "Docker tag failed." }
}

if (-not $SkipPush) {
    Write-Host "Logging in to ACR '$AcrName'..."
    az acr login --name $AcrName --output none
    if ($LASTEXITCODE -ne 0) { throw "ACR login failed." }

    Write-Host "Pushing $image..."
    docker push $image
    if ($LASTEXITCODE -ne 0) { throw "Docker push failed." }
}

# Use existing ACR credentials if available. Enable the admin account only if
# needed for Container Apps image pulls. This does not affect MCP behaviour.
$acrUser = az acr credential show --name $AcrName --query username --output tsv 2>$null
$acrPassword = az acr credential show --name $AcrName --query "passwords[0].value" --output tsv 2>$null
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
    Write-Host "Creating Container App '$AppName' in existing environment '$EnvironmentName'..."
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
Write-Host "MCP:    https://$fqdn/mcp/"
Write-Host ""
Write-Host "Test health with:"
Write-Host "Invoke-RestMethod https://$fqdn/health"
Write-Host ""
Write-Host "Test MCP with:"
Write-Host "`$env:MARKETBRIEF_MCP_URL='https://$fqdn/mcp/'; py .\Test-MarketBriefMcp.py"
