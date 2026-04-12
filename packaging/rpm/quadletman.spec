# These three defines are passed in by build-rpm.sh via --define.
# pkg_version:      X.Y.Z          (no hyphens — RPM Version field)
# pkg_release:      0.alpha.1 / 1  (pre-release sorts before stable by convention)
# pkg_full_version: X.Y.Z-alpha    (matches the source tarball filename)
%{!?pkg_version:      %global pkg_version      0.0.0}
%{!?pkg_release:      %global pkg_release      0.dev.1}
%{!?pkg_full_version: %global pkg_full_version 0.0.0.dev}

# No compiled code in the RPM itself — C extensions are compiled on the target
# system during %%post when the venv is created.
%global debug_package %{nil}
Name:           quadletman
Version:        %{pkg_version}
Release:        %{pkg_release}%{?dist}
Summary:        Web UI for managing Podman Quadlet container services

License:        MIT
URL:            https://github.com/mikkovihonen/quadletman
Source0:        %{name}-%{pkg_full_version}.tar.gz

# The RPM ships a Python wheel (arch-independent).  C extension dependencies
# (psutil, pydantic-core) are compiled on the target during %%post via pip.
BuildArch:      noarch

BuildRequires:  python3 >= 3.12
BuildRequires:  python3-pip
BuildRequires:  systemd-rpm-macros

Requires:       python3 >= 3.12
Requires:       python3-pip
Requires:       podman
Requires:       systemd
Requires:       pam
Requires:       pam-devel
Requires:       sudo
Requires:       procps-ng
Recommends:     keyutils
Recommends:     policycoreutils
Recommends:     policycoreutils-python-utils

Requires(pre):    shadow-utils
Requires(post):   systemd
Requires(preun):  systemd
Requires(postun): systemd

%description
quadletman is a lightweight web application for managing Podman Quadlet
container services on systemd-based Linux systems.

It creates a dedicated Linux system user per service (prefix qm-) and
runs containers as user-level systemd units with loginctl linger enabled.
Volumes are stored at /var/lib/quadletman/volumes/ with proper SELinux
contexts (container_file_t).

Authentication is handled entirely by Linux PAM — no separate credential
store is required. Only users in the sudo or wheel group can access the UI.


%prep
%setup -q -n %{name}-%{pkg_full_version}


%build
# Build a Python wheel from source.  The wheel is pure Python — C extension
# dependencies are installed from PyPI on the target system during %%post.
export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_QUADLETMAN="%{pkg_version}"
export SETUPTOOLS_SCM_PRETEND_VERSION="%{pkg_version}"
python3 -m venv %{_builddir}/%{name}-build-venv
%{_builddir}/%{name}-build-venv/bin/pip install --quiet --no-cache-dir \
    --disable-pip-version-check hatchling hatch-vcs
%{_builddir}/%{name}-build-venv/bin/pip install --quiet --no-cache-dir \
    --disable-pip-version-check build
%{_builddir}/%{name}-build-venv/bin/python -m build --wheel --no-isolation \
    --outdir %{_builddir}/wheel .


%install
# Ship the wheel — the venv is created at install time in %%post
install -d %{buildroot}/usr/share/%{name}
cp %{_builddir}/wheel/quadletman-%{pkg_version}-*.whl \
    %{buildroot}/usr/share/%{name}/

# Wrapper script
install -D -m 0755 /dev/stdin %{buildroot}%{_bindir}/%{name} << 'WRAPPER'
#!/bin/bash
VENV=/usr/lib/quadletman/venv
for _sp in "$VENV"/lib/python*/site-packages; do
    [ -d "$_sp" ] || continue
    export PYTHONPATH="$_sp${PYTHONPATH:+:$PYTHONPATH}"
    break
done
exec "$VENV/bin/python3" -m quadletman "$@"
WRAPPER

# systemd unit
install -D -m 0644 %{name}.service \
    %{buildroot}%{_unitdir}/%{name}.service

# Sudoers file (shipped to /usr/share, installed to /etc in %%post)
install -D -m 0440 packaging/sudoers.d/%{name} \
    %{buildroot}/usr/share/%{name}/sudoers.d/%{name}

# PAM service configuration
install -D -m 0644 packaging/pam.d/%{name} \
    %{buildroot}%{_sysconfdir}/pam.d/%{name}

# Default environment file
install -D -m 0640 packaging/%{name}.env \
    %{buildroot}%{_sysconfdir}/%{name}/%{name}.env

# State and volume directories (created at install, not shipped as files
# so they survive package removal)
install -d -m 0755 %{buildroot}%{_sharedstatedir}/%{name}
install -d -m 0755 %{buildroot}%{_sharedstatedir}/%{name}/volumes


%pre
# Ensure the shadow group exists (Fedora does not create it by default).
# The quadletman user needs it to read /etc/shadow for PAM authentication.
if ! getent group shadow >/dev/null 2>&1; then
    groupadd -r shadow
    chgrp shadow /etc/shadow
    chmod g+r /etc/shadow
fi
# Create the quadletman system user if it does not exist
getent passwd quadletman >/dev/null || \
    useradd --system --home-dir /var/lib/quadletman \
            --shell /sbin/nologin \
            --comment "quadletman service" quadletman

%post
# ---------------------------------------------------------------------------
# Create / rebuild the virtualenv with the shipped wheel.
# This ensures C extension dependencies (psutil, pydantic-core, etc.) are
# compiled against the target system's Python version.
# Requires internet access on first install (pip fetches deps from PyPI).
# ---------------------------------------------------------------------------
VENV=/usr/lib/%{name}/venv
WHEEL_DIR=/usr/share/%{name}

# Stop the service before rebuilding the venv (upgrade case).
systemctl stop %{name}.service 2>/dev/null || :

# Recreate venv on every install/upgrade so the Python version always matches.
rm -rf "$VENV"
python3 -m venv "$VENV"
if ! "$VENV/bin/pip" install --quiet --no-cache-dir --disable-pip-version-check \
    "$WHEEL_DIR"/quadletman-*.whl; then
    echo "ERROR: pip install failed — quadletman will not start." >&2
    echo "Ensure internet access is available and retry with:" >&2
    echo "  $VENV/bin/pip install $WHEEL_DIR/quadletman-*.whl" >&2
fi

# Restore SELinux contexts on compiled extensions so they can be dlopen'd.
if command -v restorecon &>/dev/null; then
    restorecon -Rv "$VENV/" &>/dev/null || :
fi

%systemd_post %{name}.service
# Add quadletman to supplementary groups for PAM and journal access
for grp in shadow systemd-journal; do
    getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" quadletman 2>/dev/null || :
done
# Ensure subuid/subgid ranges for rootless podman (needed in non-root mode)
grep -q "^quadletman:" /etc/subuid 2>/dev/null || usermod --add-subuids 100000-165535 quadletman 2>/dev/null || :
grep -q "^quadletman:" /etc/subgid 2>/dev/null || usermod --add-subgids 100000-165535 quadletman 2>/dev/null || :
# Ensure state directories exist with correct ownership
install -d -m 0755 -o quadletman -g quadletman %{_sharedstatedir}/%{name}
install -d -m 0755 -o quadletman -g quadletman %{_sharedstatedir}/%{name}/volumes
install -d -m 0750 -o quadletman -g quadletman /var/log/quadletman
# Migrate ownership from root if upgrading
chown quadletman:quadletman %{_sharedstatedir}/%{name}/quadletman.db 2>/dev/null || :
# Install sudoers file
install -m 0440 /usr/share/%{name}/sudoers.d/%{name} /etc/sudoers.d/%{name} 2>/dev/null || :


%preun
%systemd_preun %{name}.service


%postun
%systemd_postun_with_restart %{name}.service
# Clean up the venv on full removal (not on upgrade)
if [ "$1" -eq 0 ]; then
    rm -rf /usr/lib/%{name}/venv
fi


%files
/usr/share/%{name}/
%{_bindir}/%{name}
%{_unitdir}/%{name}.service
%config(noreplace) %{_sysconfdir}/pam.d/%{name}
%config(noreplace) %attr(640,root,quadletman) %{_sysconfdir}/%{name}/%{name}.env
%dir %{_sharedstatedir}/%{name}
%dir %{_sharedstatedir}/%{name}/volumes


%changelog
* %(date "+%a %b %d %Y") quadletman packager <packager@example.com> - %{pkg_version}-%{pkg_release}
- See CHANGELOG.md for release notes.
