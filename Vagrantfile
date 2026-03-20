# -*- mode: ruby -*-
# vi: set ft=ruby :
#
# Smoke-test VMs for quadletman packages.
# See docs/packaging.md for full instructions.
#
# Usage:
#   vagrant up fedora              # Fedora RPM + SELinux smoke test
#   vagrant up ubuntu              # Ubuntu DEB smoke test
#   vagrant up                     # both VMs
#   vagrant rsync && vagrant provision   # push code changes then re-test
#   vagrant ssh fedora             # shell into the Fedora VM
#   vagrant ssh ubuntu             # shell into the Ubuntu VM
#   vagrant destroy -f             # tear down all VMs

RSYNC_EXCLUDES = [
  ".git/",
  ".venv/",
  "__pycache__/",
  "*.egg-info/",
  "dist/",
  "build/",
  "node_modules/",
]

Vagrant.configure("2") do |config|

  # ---------- Fedora (RPM + SELinux) ----------
  config.vm.define "fedora", primary: true do |fedora|
    fedora.vm.box      = "bento/fedora-41"
    fedora.vm.hostname = "quadletman-smoke-fedora"

    fedora.vm.network "forwarded_port", guest: 8080, host: 8081, host_ip: "127.0.0.1"

    fedora.vm.provider "libvirt" do |lv|
      lv.memory = 2048
      lv.cpus   = 2
    end

    fedora.vm.provider "virtualbox" do |vb|
      vb.name   = "quadletman-smoke-fedora"
      vb.memory = 2048
      vb.cpus   = 2
      vb.customize ["modifyvm", :id, "--ioapic", "on"]
    end

    fedora.vm.synced_folder ".", "/vagrant/quadletman",
      type: "rsync",
      rsync__exclude: RSYNC_EXCLUDES

    fedora.vm.provision "shell", path: "packaging/smoke-test-vm.sh"
  end

  # ---------- Ubuntu (DEB) ----------
  config.vm.define "ubuntu", autostart: false do |ubuntu|
    ubuntu.vm.box      = "bento/ubuntu-24.04"
    ubuntu.vm.hostname = "quadletman-smoke-ubuntu"

    ubuntu.vm.network "forwarded_port", guest: 8080, host: 8082, host_ip: "127.0.0.1"

    ubuntu.vm.provider "libvirt" do |lv|
      lv.memory = 2048
      lv.cpus   = 2
    end

    ubuntu.vm.provider "virtualbox" do |vb|
      vb.name   = "quadletman-smoke-ubuntu"
      vb.memory = 2048
      vb.cpus   = 2
      vb.customize ["modifyvm", :id, "--ioapic", "on"]
    end

    ubuntu.vm.synced_folder ".", "/vagrant/quadletman",
      type: "rsync",
      rsync__exclude: RSYNC_EXCLUDES

    ubuntu.vm.provision "shell", path: "packaging/smoke-test-vm-deb.sh"
  end

end
