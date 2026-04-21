// Persistent self-hosted GitHub Actions runner VMs
targetScope = 'resourceGroup'

@description('Location for all resources')
param location string = 'eastus'

@description('SSH public key for Linux VMs')
@secure()
param sshPublicKey string

@description('Admin password for Windows VM')
@secure()
param windowsAdminPassword string

@description('Object ID of the Key Vault admin user')
param kvAdminObjectId string

@description('Admin username for all runner VMs')
param adminUsername string = 'runneradmin'

// Variables
var prefix = 'quicksand'
var vnetName = 'vnet-${prefix}-runners'
var nsgName = 'nsg-${prefix}-runners'
var kvName = 'kv-${prefix}-runners'
var shutdownTime = '0200' // 9pm EST = 02:00 UTC

// Runners
var linuxRunners = [
  { name: 'quicksand-runner-x64',    vmSize: 'Standard_D4s_v5',  sku: 'server',       arch: 'x64'   }
  { name: 'quicksand-runner-arm64',  vmSize: 'Standard_D4ps_v5', sku: 'server-arm64', arch: 'arm64' }
]

var windowsRunner = {
  name: 'quicksand-runner-win'
  vmSize: 'Standard_D4s_v5'
}

// NSG — deny all inbound
resource nsg 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: nsgName
  location: location
  properties: {
    securityRules: [
      {
        name: 'DenyAllInbound'
        properties: {
          priority: 4096
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

// VNet
resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.0.0.0/16'] }
    subnets: [
      {
        name: 'snet-runners'
        properties: {
          addressPrefix: '10.0.0.0/24'
          networkSecurityGroup: { id: nsg.id }
        }
      }
    ]
  }
}

// ---------- Key Vault ----------

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: false
    accessPolicies: [
      {
        tenantId: subscription().tenantId
        objectId: kvAdminObjectId
        permissions: {
          secrets: ['all']
          keys: ['all']
          certificates: ['all']
        }
      }
    ]
  }
}

resource secretWinPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'windows-admin-password'
  properties: { value: windowsAdminPassword }
}

resource secretSshKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'ssh-public-key'
  properties: { value: sshPublicKey }
}

// ---------- Linux runners ----------

resource linuxPips 'Microsoft.Network/publicIPAddresses@2023-11-01' = [
  for runner in linuxRunners: {
    name: 'pip-${runner.name}'
    location: location
    sku: { name: 'Standard' }
    properties: {
      publicIPAllocationMethod: 'Static'
    }
  }
]

resource linuxNics 'Microsoft.Network/networkInterfaces@2023-11-01' = [
  for (runner, i) in linuxRunners: {
    name: 'nic-${runner.name}'
    location: location
    properties: {
      ipConfigurations: [
        {
          name: 'ipconfig1'
          properties: {
            subnet: { id: vnet.properties.subnets[0].id }
            privateIPAllocationMethod: 'Dynamic'
            publicIPAddress: { id: linuxPips[i].id }
          }
        }
      ]
    }
  }
]

resource linuxVms 'Microsoft.Compute/virtualMachines@2024-03-01' = [
  for (runner, i) in linuxRunners: {
    name: runner.name
    location: location
    identity: runner.arch == 'x64' ? { type: 'SystemAssigned' } : null
    properties: {
      hardwareProfile: { vmSize: runner.vmSize }
      storageProfile: {
        imageReference: {
          publisher: 'Canonical'
          offer: 'ubuntu-24_04-lts'
          sku: runner.sku
          version: 'latest'
        }
        osDisk: {
          createOption: 'FromImage'
          diskSizeGB: 256
          managedDisk: { storageAccountType: 'Premium_LRS' }
          deleteOption: 'Delete'
        }
      }
      networkProfile: {
        networkInterfaces: [
          { id: linuxNics[i].id, properties: { deleteOption: 'Delete' } }
        ]
      }
      osProfile: {
        computerName: take(runner.name, 15)
        adminUsername: adminUsername
        linuxConfiguration: {
          disablePasswordAuthentication: true
          ssh: {
            publicKeys: [
              {
                path: '/home/${adminUsername}/.ssh/authorized_keys'
                keyData: sshPublicKey
              }
            ]
          }
        }
        customData: base64(loadTextContent('cloud-init.yaml'))
      }
    }
  }
]

resource linuxShutdown 'Microsoft.DevTestLab/schedules@2018-09-15' = [
  for (runner, i) in linuxRunners: {
    name: 'shutdown-computevm-${runner.name}'
    location: location
    properties: {
      status: 'Enabled'
      taskType: 'ComputeVmShutdownTask'
      dailyRecurrence: { time: shutdownTime }
      timeZoneId: 'UTC'
      targetResourceId: linuxVms[i].id
    }
  }
]

// ---------- Windows runner ----------

resource winNic 'Microsoft.Network/networkInterfaces@2023-11-01' = {
  name: 'nic-${windowsRunner.name}'
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: vnet.properties.subnets[0].id }
          privateIPAllocationMethod: 'Dynamic'
        }
      }
    ]
  }
}

resource winVm 'Microsoft.Compute/virtualMachines@2024-03-01' = {
  name: windowsRunner.name
  location: location
  properties: {
    hardwareProfile: { vmSize: windowsRunner.vmSize }
    storageProfile: {
      imageReference: {
        publisher: 'MicrosoftWindowsServer'
        offer: 'WindowsServer'
        sku: '2022-datacenter-g2'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: 256
        managedDisk: { storageAccountType: 'Premium_LRS' }
        deleteOption: 'Delete'
      }
    }
    networkProfile: {
      networkInterfaces: [
        { id: winNic.id, properties: { deleteOption: 'Delete' } }
      ]
    }
    osProfile: {
      computerName: 'runner-win'
      adminUsername: adminUsername
      adminPassword: windowsAdminPassword
    }
  }
}

// Install pwsh, Git, and runner agent on Windows
resource winSetup 'Microsoft.Compute/virtualMachines/extensions@2024-03-01' = {
  parent: winVm
  name: 'setup-runner'
  location: location
  properties: {
    publisher: 'Microsoft.Compute'
    type: 'CustomScriptExtension'
    typeHandlerVersion: '1.10'
    autoUpgradeMinorVersion: true
    protectedSettings: {
      commandToExecute: 'powershell -ExecutionPolicy Unrestricted -Command "Invoke-WebRequest -Uri https://github.com/PowerShell/PowerShell/releases/download/v7.4.7/PowerShell-7.4.7-win-x64.msi -OutFile $env:TEMP\\pwsh.msi; Start-Process msiexec.exe -ArgumentList \'/i\',$env:TEMP\'\\pwsh.msi\',\'/quiet\',\'ADD_PATH=1\' -Wait; Invoke-WebRequest -Uri https://github.com/git-for-windows/git/releases/download/v2.48.1.windows.1/Git-2.48.1-64-bit.exe -OutFile $env:TEMP\\git.exe; Start-Process $env:TEMP\\git.exe -ArgumentList \'/VERYSILENT\',\'/NORESTART\' -Wait"'
    }
  }
}

resource winShutdown 'Microsoft.DevTestLab/schedules@2018-09-15' = {
  name: 'shutdown-computevm-${windowsRunner.name}'
  location: location
  properties: {
    status: 'Enabled'
    taskType: 'ComputeVmShutdownTask'
    dailyRecurrence: { time: shutdownTime }
    timeZoneId: 'UTC'
    targetResourceId: winVm.id
  }
}
