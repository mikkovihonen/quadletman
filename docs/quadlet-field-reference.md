# Quadlet Field Reference — Canonical Podman Keys

This document is the authoritative reference for all Quadlet unit file keys supported by
Podman. Use it to cross-check that quadletman's internal model field names map correctly to
these canonical keys, and that no incorrect namespacing (e.g. `qm_` prefixes) has been
introduced.

**Upstream reference:** https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html

**Note:** The upstream docs page covers Container, Pod, Volume, Network, Build, and Kube
sections in detail. Image and Artifact sections may be documented on separate pages or in
newer Podman versions. The field lists below were compiled from both the upstream docs and
the Podman source/man pages.

## How to use this document

When refactoring or renaming model fields:

1. Every field that represents a Quadlet key must map to exactly one entry below
2. The `quadlet_key` in the `VersionSpan` annotation must match the canonical key name exactly
3. The Python field name should be a snake_case version of the canonical key (e.g.
   `AuthFile` → `auth_file`, `TLSVerify` → `tls_verify`)
4. Fields that are **not** in this document are either:
   - **quadletman internal** (e.g. `id`, `compartment_id`, `created_at`, `name`) — these
     are DB/app concepts, not written to unit files
   - **systemd keys** (e.g. fields from `[Unit]`, `[Service]`, `[Install]`, `[Timer]`
     sections) — these are third-party, not Quadlet-specific

## Cross-unit section: [Quadlet]

These keys can appear in any unit type's `[Quadlet]` section:

```
DefaultDependencies
```

---

## [Artifact] section (`.artifact` files)

```
Artifact
AuthFile
CertDir
ContainersConfModule
Creds
DecryptionKey
GlobalArgs
PodmanArgs
Quiet
Retry
RetryDelay
ServiceName
TLSVerify
```

**Total: 13 keys**

---

## [Image] section (`.image` files)

```
AllTags
Arch
AuthFile
CertDir
ContainersConfModule
Creds
DecryptionKey
GlobalArgs
Image
ImageTag
OS
PodmanArgs
Policy
Retry
RetryDelay
ServiceName
TLSVerify
Variant
```

**Total: 18 keys**

---

## [Build] section (`.build` files)

```
Annotation
Arch
AuthFile
BuildArg
ContainersConfModule
DNS
DNSOption
DNSSearch
Environment
File
ForceRM
GlobalArgs
GroupAdd
IgnoreFile
ImageTag
Label
Network
PodmanArgs
Pull
Retry
RetryDelay
Secret
ServiceName
SetWorkingDirectory
Target
TLSVerify
Variant
Volume
```

**Total: 28 keys**

---

## [Volume] section (`.volume` files)

```
ContainersConfModule
Copy
Device
Driver
GID
GlobalArgs
Group
Image
Label
Options
PodmanArgs
ServiceName
Type
UID
User
VolumeName
```

**Total: 16 keys**

---

## [Network] section (`.network` files)

```
ContainersConfModule
DisableDNS
DNS
Driver
Gateway
GlobalArgs
InterfaceName
Internal
IPAMDriver
IPRange
IPv6
Label
NetworkDeleteOnStop
NetworkName
Options
PodmanArgs
ServiceName
Subnet
```

**Total: 18 keys**

---

## [Kube] section (`.kube` files)

```
AutoUpdate
ConfigMap
ContainersConfModule
ExitCodePropagation
GlobalArgs
KubeDownForce
LogDriver
Network
PodmanArgs
PublishPort
ServiceName
SetWorkingDirectory
UserNS
Yaml
```

**Total: 14 keys**

---

## [Pod] section (`.pod` files)

```
AddHost
ContainersConfModule
DNS
DNSOption
DNSSearch
ExitPolicy
GIDMap
GlobalArgs
HostName
IP
IP6
Label
Network
NetworkAlias
PodmanArgs
PodName
PublishPort
ServiceName
ShmSize
StopTimeout
SubGIDMap
SubUIDMap
UIDMap
UserNS
Volume
```

**Total: 25 keys**

---

## [Container] section (`.container` files)

```
AddCapability
AddDevice
AddHost
Annotation
AppArmor
AutoUpdate
CgroupsMode
ContainerName
ContainersConfModule
DNS
DNSOption
DNSSearch
DropCapability
Entrypoint
Environment
EnvironmentFile
EnvironmentHost
Exec
ExposeHostPort
GIDMap
GlobalArgs
Group
GroupAdd
HealthCmd
HealthInterval
HealthLogDestination
HealthMaxLogCount
HealthMaxLogSize
HealthOnFailure
HealthRetries
HealthStartPeriod
HealthStartupCmd
HealthStartupInterval
HealthStartupRetries
HealthStartupSuccess
HealthStartupTimeout
HealthTimeout
HostName
HttpProxy
Image
IP
IP6
Label
LogDriver
LogOpt
Mask
Memory
Mount
Network
NetworkAlias
NoNewPrivileges
Notify
PidsLimit
Pod
PodmanArgs
PublishPort
Pull
ReadOnly
ReadOnlyTmpfs
ReloadCmd
ReloadSignal
Retry
RetryDelay
Rootfs
RunInit
SeccompProfile
Secret
SecurityLabelDisable
SecurityLabelFileType
SecurityLabelLevel
SecurityLabelNested
SecurityLabelType
ServiceName
ShmSize
StartWithPod
StopSignal
StopTimeout
SubGIDMap
SubUIDMap
Sysctl
Timezone
Tmpfs
UIDMap
Ulimit
Unmask
User
UserNS
Volume
WorkingDir
```

**Total: 88 keys**

---

## Field origin classification guide

When annotating model fields, classify each as one of:

| Origin | Meaning | Examples |
|--------|---------|---------|
| `quadlet` | Key from a Quadlet section listed above | `Image`, `Volume`, `DNS`, `PublishPort` |
| `systemd` | Key from `[Unit]`, `[Service]`, `[Install]`, `[Timer]` sections | `Description`, `After`, `Wants`, `TimeoutStartSec` |
| `quadletman` | Internal to quadletman, not written to unit files | `id`, `compartment_id`, `created_at`, `name` |

### How to decide

1. Is the field in the lists above? → `quadlet`
2. Is the field a standard systemd unit file key? → `systemd`
3. Is the field only used by quadletman's DB/UI/logic? → `quadletman`

### Common shared keys across unit types

These keys appear in multiple unit types — the Python field name should be the same
everywhere they appear:

| Key | Appears in |
|-----|-----------|
| `ContainersConfModule` | All types |
| `GlobalArgs` | All types |
| `PodmanArgs` | All types |
| `ServiceName` | All types |
| `Retry` | Artifact, Image, Build, Container |
| `RetryDelay` | Artifact, Image, Build, Container |
| `TLSVerify` | Artifact, Image, Build |
| `AuthFile` | Artifact, Image, Build |
| `CertDir` | Artifact, Image |
| `Creds` | Artifact, Image |
| `DecryptionKey` | Artifact, Image |
| `DNS` | Build, Network, Pod, Container |
| `DNSOption` | Build, Pod, Container |
| `DNSSearch` | Build, Pod, Container |
| `Network` | Build, Kube, Pod, Container |
| `Volume` | Build, Pod, Container |
| `PublishPort` | Kube, Pod, Container |
| `UserNS` | Kube, Pod, Container |
| `Label` | Build, Volume, Network, Pod, Container |
| `Image` | Image, Volume, Container |
| `ImageTag` | Image, Build |
| `Arch` | Image, Build |
| `Variant` | Image, Build |
| `Pull` | Build, Container |
| `Secret` | Build, Container |
| `GroupAdd` | Build, Container |
| `Environment` | Build, Container |
| `Driver` | Volume, Network |
| `Options` | Volume, Network |
| `IP` | Pod, Container |
| `IP6` | Pod, Container |
| `NetworkAlias` | Pod, Container |
| `ShmSize` | Pod, Container |
| `StopTimeout` | Pod, Container |
| `GIDMap` | Pod, Container |
| `UIDMap` | Pod, Container |
| `SubGIDMap` | Pod, Container |
| `SubUIDMap` | Pod, Container |
| `HostName` | Pod, Container |
| `AutoUpdate` | Kube, Container |
| `LogDriver` | Kube, Container |
| `SetWorkingDirectory` | Build, Kube |
| `AddHost` | Pod, Container |
| `ExitPolicy` | Pod |
| `PodName` | Pod |

### Expected Python field name mapping

The snake_case conversion should follow these rules:
- Simple CamelCase → snake_case: `AuthFile` → `auth_file`
- Acronyms treated as words: `DNS` → `dns`, `IP` → `ip`, `TLS` → `tls`
- Consecutive caps split naturally: `DNSOption` → `dns_option`, `TLSVerify` → `tls_verify`
- Numbers stay attached: `IP6` → `ip6`

Exceptions (if any) should be documented explicitly in the model with a comment explaining
the divergence.
