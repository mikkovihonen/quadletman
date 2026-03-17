# These three defines are passed in by build-rpm.sh via --define.
# pkg_version:      X.Y.Z          (no hyphens — RPM Version field)
# pkg_release:      0.alpha.1 / 1  (pre-release sorts before stable by convention)
# pkg_full_version: X.Y.Z-alpha    (matches the source tarball filename)
%{!?pkg_version:      %global pkg_version      0.0.0}
%{!?pkg_release:      %global pkg_release      0.dev.1}
%{!?pkg_full_version: %global pkg_full_version 0.0.0.dev}
Name:           quadletman
Version:        %{pkg_version}
Release:        %{pkg_release}%{?dist}
Summary:        Web UI for managing Podman Quadlet container services

License:        MIT
URL:            https://github.com/mikkovihonen/quadletman
Source0:        %{name}-%{pkg_full_version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3 >= 3.11
BuildRequires:  python3-pip
BuildRequires:  python3-virtualenv
BuildRequires:  systemd-rpm-macros

Requires:       python3 >= 3.11
Requires:       podman
Requires:       systemd
Requires:       pam

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
# hatch-vcs reads the version from git, but rpmbuild unpacks a plain tarball.
# Set the pretend version so hatchling does not try to query git.
%global scm_pretend_ver %{pkg_version}
export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_QUADLETMAN="%{pkg_version}"
# Create a virtualenv at a build-time path; we will copy it to %{buildroot}
python3 -m venv %{_builddir}/%{name}-venv
%{_builddir}/%{name}-venv/bin/pip install --quiet --no-cache-dir \
    --disable-pip-version-check .


%install
# Copy the built virtualenv into the final install tree
install -d %{buildroot}/usr/lib/%{name}
cp -a %{_builddir}/%{name}-venv %{buildroot}/usr/lib/%{name}/venv

# Fix pyvenv.cfg home to point to the system Python directory so that
# /usr/lib/quadletman/venv/bin/python3 resolves correctly at runtime.
# (The symlink in bin/ already points to the real python3; this is belt-and-
# suspenders for distlib compatibility.)
SYSBIN=$(dirname $(readlink -f %{_builddir}/%{name}-venv/bin/python3))
sed -i "s|^home = .*|home = ${SYSBIN}|" \
    %{buildroot}/usr/lib/%{name}/venv/pyvenv.cfg

# Wrapper script — we call the venv's Python directly so no shebang
# rewriting is needed inside the venv itself.
install -D -m 0755 /dev/stdin %{buildroot}%{_bindir}/%{name} << 'WRAPPER'
#!/bin/bash
exec /usr/lib/quadletman/venv/bin/python3 -m quadletman "$@"
WRAPPER

# systemd unit
install -D -m 0644 %{name}.service \
    %{buildroot}%{_unitdir}/%{name}.service

# State and volume directories (created at install, not shipped as files
# so they survive package removal)
install -d -m 0755 %{buildroot}%{_sharedstatedir}/%{name}
install -d -m 0755 %{buildroot}%{_sharedstatedir}/%{name}/volumes


%post
%systemd_post %{name}.service
# Ensure state directories exist (idempotent)
install -d -m 0755 %{_sharedstatedir}/%{name}
install -d -m 0755 %{_sharedstatedir}/%{name}/volumes


%preun
%systemd_preun %{name}.service


%postun
%systemd_postun_with_restart %{name}.service


%files
/usr/lib/%{name}/venv/
%{_bindir}/%{name}
%{_unitdir}/%{name}.service
%dir %{_sharedstatedir}/%{name}
%dir %{_sharedstatedir}/%{name}/volumes


%changelog
* %(date "+%a %b %d %Y") quadletman packager <packager@example.com> - %{pkg_version}-%{pkg_release}
- See CHANGELOG.md for release notes.
