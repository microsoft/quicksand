# Quicksand Ubuntu 24.04 Base Image
#
# This Dockerfile creates a minimal Ubuntu 24.04 system suitable for
# running as a quicksand VM guest. Uses a Rust-based agent for minimal footprint.

# =============================================================================
# Stage 1: Build the Rust agent
# =============================================================================
FROM rust:bookworm AS agent-builder

WORKDIR /build
COPY agent/Cargo.toml agent/Cargo.lock* ./
COPY agent/src ./src

RUN cargo build --release && \
    strip /build/target/release/quicksand-guest-agent

# =============================================================================
# Stage 2: Create the Ubuntu VM image
# =============================================================================
FROM ubuntu:24.04

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install kernel, modules, generate initramfs, install remaining packages
# All in one layer to minimize image size.
RUN apt-get update && apt-get install -y --no-install-recommends \
    linux-image-virtual \
    && KVER=$(ls /lib/modules/ | head -1) \
    && apt-get install -y --no-install-recommends linux-modules-extra-${KVER} \
    # Generate initramfs (not done automatically in Docker).
    # initramfs-tools is removed after — it's not needed at runtime.
    && apt-get install -y --no-install-recommends initramfs-tools \
    && update-initramfs -c -k ${KVER} \
    && apt-get remove --purge -y initramfs-tools initramfs-tools-core \
        initramfs-tools-bin busybox-initramfs klibc-utils libklibc \
        dracut-install cpio \
    # Remaining packages
    && apt-get install -y --no-install-recommends \
        systemd systemd-sysv \
        iproute2 iputils-ping \
        sudo curl ca-certificates \
        cifs-utils cloud-guest-utils e2fsprogs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/* /var/tmp/*

# Create quicksand user with passwordless sudo
RUN useradd -m -s /bin/bash -G sudo quicksand
COPY quicksand-sudoers /etc/sudoers.d/quicksand
RUN chmod 440 /etc/sudoers.d/quicksand

# Ensure 9p kernel modules are loaded at boot
RUN echo "9p" >> /etc/modules \
    && echo "9pnet" >> /etc/modules \
    && echo "9pnet_virtio" >> /etc/modules

# Configure networking with systemd-networkd
RUN mkdir -p /etc/systemd/network
COPY 80-dhcp.network /etc/systemd/network/80-dhcp.network
RUN systemctl enable systemd-networkd

# Configure systemd for VM environment
# NOTE: We disable systemd-resolved because it can cause boot delays when
# restrict-network=True. Instead, we use a static resolv.conf via tmpfiles.d.
RUN systemctl set-default multi-user.target \
    && systemctl disable systemd-resolved \
    && systemctl mask systemd-resolved.service \
    && systemctl mask NetworkManager-wait-online.service \
    || true

# Use static DNS config pointing to QEMU SLIRP DNS server (10.0.2.3)
# tmpfiles.d creates the symlink at boot since /etc/resolv.conf is bind-mounted
# during Docker build and can't be modified directly.
COPY resolv.conf /etc/resolv.conf.quicksand
COPY tmpfiles-resolv.conf /etc/tmpfiles.d/resolv.conf

# Set hostname
COPY hostname /etc/hostname

# /etc/hosts is required for sudo to work without DNS (hostname resolution)
# Docker bind-mounts /etc/hosts during build, so we copy to a different location
# and use tmpfiles.d to copy it at boot time
COPY hosts /etc/hosts.quicksand
COPY tmpfiles-hosts.conf /etc/tmpfiles.d/hosts.conf

# Copy the Rust agent binary
COPY --from=agent-builder /build/target/release/quicksand-guest-agent /usr/local/bin/quicksand-guest-agent
RUN chmod +x /usr/local/bin/quicksand-guest-agent

# Install systemd service for the agent
COPY quicksand-guest-agent.service /etc/systemd/system/quicksand-guest-agent.service
RUN systemctl enable quicksand-guest-agent

CMD ["/sbin/init"]
