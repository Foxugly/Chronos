#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Manage Exchange Online proxy address aliases on a shared mailbox.

.DESCRIPTION
    Connects to Exchange Online via Connect-ExchangeOnline using app-only
    certificate authentication (Azure AD service principal), then performs
    add / remove / list operations on the EmailAddresses collection of a
    target mailbox. Output is structured JSON on stdout, suitable for parsing
    from Python via subprocess.

.PARAMETER Action
    One of: add, remove, list

.PARAMETER Mailbox
    Primary SMTP address (or UPN) of the target mailbox, e.g. shared@contoso.com

.PARAMETER Alias
    Full email address of the alias to add or remove. Required for add/remove,
    ignored for list.

.NOTES
    Required environment variables:
      EXCHANGE_APP_ID            : Azure AD application (client) ID
      EXCHANGE_TENANT            : Tenant primary domain (e.g. contoso.onmicrosoft.com).
                                   Must be the domain, NOT the Directory ID GUID.
      EXCHANGE_CERT_THUMBPRINT   : Thumbprint of cert installed in the user's
                                   cert store. WINDOWS ONLY — Connect-ExchangeOnline
                                   on Linux pwsh does not expose
                                   -CertificateThumbprint. Leave empty on Linux.
                                   OR
      EXCHANGE_CERT_FILE_PATH    : Path to a PFX/PEM file. Required on Linux.
      EXCHANGE_CERT_PASSWORD     : Password for the PFX (optional)

    If both EXCHANGE_CERT_THUMBPRINT and EXCHANGE_CERT_FILE_PATH are set,
    the thumbprint takes precedence (legacy Windows behavior).

.OUTPUTS
    JSON object on stdout:
      { "success": true,  "data": <result> }
      { "success": false, "error_code": "<code>", "error": "<message>" }

    Error codes:
      missing_param, missing_env, auth_failed, mailbox_not_found,
      alias_already_exists, alias_not_found, exchange_error
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('add', 'remove', 'list')]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$Mailbox,

    [Parameter(Mandatory = $false)]
    [string]$Alias
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'
$WarningPreference     = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'

function Write-Result {
    param(
        [Parameter(Mandatory = $true)] [bool]$Success,
        [object]$Data,
        [string]$ErrorCode,
        [string]$ErrorMessage
    )
    $payload = [ordered]@{ success = $Success }
    if ($Success) {
        $payload['data'] = $Data
    } else {
        $payload['error_code'] = $ErrorCode
        $payload['error']      = $ErrorMessage
    }
    # -Compress keeps the JSON on a single line for easy parsing.
    Write-Output ($payload | ConvertTo-Json -Depth 6 -Compress)
}

function Get-RequiredEnv {
    param([string]$Name)
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "missing_env:$Name"
    }
    return $value
}

# -----------------------------------------------------------------------------
# Argument validation
# -----------------------------------------------------------------------------

if (($Action -in @('add', 'remove')) -and [string]::IsNullOrWhiteSpace($Alias)) {
    Write-Result -Success $false -ErrorCode 'missing_param' -ErrorMessage "Alias is required for action '$Action'."
    exit 2
}

# -----------------------------------------------------------------------------
# Connect to Exchange Online (app-only auth)
# -----------------------------------------------------------------------------

$connected = $false
try {
    try {
        $appId  = Get-RequiredEnv 'EXCHANGE_APP_ID'
        $tenant = Get-RequiredEnv 'EXCHANGE_TENANT'
    } catch {
        $missing = ($_.Exception.Message -replace '^missing_env:', '')
        Write-Result -Success $false -ErrorCode 'missing_env' -ErrorMessage "Required env var not set: $missing"
        exit 3
    }

    $thumbprint = [Environment]::GetEnvironmentVariable('EXCHANGE_CERT_THUMBPRINT')
    $certPath   = [Environment]::GetEnvironmentVariable('EXCHANGE_CERT_FILE_PATH')
    $certPwd    = [Environment]::GetEnvironmentVariable('EXCHANGE_CERT_PASSWORD')

    $connectParams = @{
        AppId        = $appId
        Organization = $tenant
        ShowBanner   = $false
        ErrorAction  = 'Stop'
    }

    if (-not [string]::IsNullOrWhiteSpace($thumbprint)) {
        $connectParams['CertificateThumbprint'] = $thumbprint
    } elseif (-not [string]::IsNullOrWhiteSpace($certPath)) {
        $connectParams['CertificateFilePath'] = $certPath
        if (-not [string]::IsNullOrWhiteSpace($certPwd)) {
            $connectParams['CertificatePassword'] = (ConvertTo-SecureString -String $certPwd -AsPlainText -Force)
        }
    } else {
        Write-Result -Success $false -ErrorCode 'missing_env' `
            -ErrorMessage 'Either EXCHANGE_CERT_THUMBPRINT or EXCHANGE_CERT_FILE_PATH must be set.'
        exit 3
    }

    try {
        Connect-ExchangeOnline @connectParams | Out-Null
        $connected = $true
    } catch {
        Write-Result -Success $false -ErrorCode 'auth_failed' -ErrorMessage $_.Exception.Message
        exit 4
    }

    # -------------------------------------------------------------------------
    # Action dispatch
    # -------------------------------------------------------------------------

    try {
        $mbx = Get-Mailbox -Identity $Mailbox -ErrorAction Stop
    } catch {
        Write-Result -Success $false -ErrorCode 'mailbox_not_found' `
            -ErrorMessage "Mailbox '$Mailbox' not found: $($_.Exception.Message)"
        exit 5
    }

    switch ($Action) {

        'list' {
            $addresses = @($mbx.EmailAddresses)
            Write-Result -Success $true -Data $addresses
            exit 0
        }

        'add' {
            $smtpEntry = "smtp:$Alias"
            $existing  = @($mbx.EmailAddresses) | Where-Object { $_ -ieq $smtpEntry -or $_ -ieq "SMTP:$Alias" }
            if ($existing.Count -gt 0) {
                Write-Result -Success $false -ErrorCode 'alias_already_exists' `
                    -ErrorMessage "Alias '$Alias' already exists on mailbox '$Mailbox'."
                exit 6
            }
            try {
                Set-Mailbox -Identity $Mailbox -EmailAddresses @{ add = $Alias } -ErrorAction Stop
            } catch {
                Write-Result -Success $false -ErrorCode 'exchange_error' -ErrorMessage $_.Exception.Message
                exit 7
            }
            Write-Result -Success $true -Data @{ mailbox = $Mailbox; alias = $Alias; action = 'added' }
            exit 0
        }

        'remove' {
            $smtpEntry   = "smtp:$Alias"
            $primarySmtp = "SMTP:$Alias"
            $existing    = @($mbx.EmailAddresses) | Where-Object { $_ -ieq $smtpEntry -or $_ -ieq $primarySmtp }
            if ($existing.Count -eq 0) {
                Write-Result -Success $false -ErrorCode 'alias_not_found' `
                    -ErrorMessage "Alias '$Alias' not found on mailbox '$Mailbox'."
                exit 8
            }
            try {
                Set-Mailbox -Identity $Mailbox -EmailAddresses @{ remove = $Alias } -ErrorAction Stop
            } catch {
                Write-Result -Success $false -ErrorCode 'exchange_error' -ErrorMessage $_.Exception.Message
                exit 7
            }
            Write-Result -Success $true -Data @{ mailbox = $Mailbox; alias = $Alias; action = 'removed' }
            exit 0
        }
    }

} catch {
    Write-Result -Success $false -ErrorCode 'exchange_error' -ErrorMessage $_.Exception.Message
    exit 1
} finally {
    if ($connected) {
        try {
            Disconnect-ExchangeOnline -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
        } catch {
            # Disconnect best-effort; do not mask the original error.
        }
    }
}
