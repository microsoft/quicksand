# Quicksand Alpine Linux Base Image
#
# This Dockerfile creates a minimal Alpine Linux system suitable for
# running as a quicksand VM guest. Uses a Rust-based agent for minimal size.

# =============================================================================
# Stage 1: Build the Rust agent
# =============================================================================
FROM rust:alpine AS agent-builder

# Install build dependencies
RUN apk add --no-cache musl-dev

# Set up the build directory
WORKDIR /build

# Copy agent source
COPY agent/Cargo.toml agent/Cargo.lock* ./
COPY agent/src ./src

# Build the agent (release mode, optimized for size)
RUN cargo build --release && \
    strip /build/target/release/quicksand-guest-agent

# =============================================================================
# Stage 2: Create the minimal VM image
# =============================================================================
FROM alpine:3.23

# Install kernel and essential packages (minimal set - no Python!)
RUN apk add --no-cache \
    # Kernel (includes virtio modules)
    linux-virt \
    # Init system
    openrc \
    busybox-openrc \
    # Networking
    dhcpcd \
    curl \
    ca-certificates \
    # Utilities
    sudo \
    # Disk resize tools (for disk_size config option)
    e2fsprogs-extra \
    cloud-utils-growpart

# Create quicksand user with passwordless sudo
RUN adduser -D -s /bin/sh quicksand
COPY quicksand-sudoers /etc/sudoers.d/quicksand
RUN chmod 440 /etc/sudoers.d/quicksand

# Ensure 9p kernel modules are loaded at boot
RUN echo "9p" >> /etc/modules \
    && echo "9pnet" >> /etc/modules \
    && echo "9pnet_virtio" >> /etc/modules

# Configure networking with static IP (QEMU slirp always uses 10.0.2.0/24)
RUN mkdir -p /etc/network
COPY interfaces /etc/network/interfaces

# Enable essential services at boot
RUN rc-update add devfs sysinit \
    && rc-update add dmesg sysinit \
    && (rc-update add mdev sysinit || true) \
    && rc-update add hwclock boot \
    && rc-update add modules boot \
    && rc-update add sysctl boot \
    && rc-update add hostname boot \
    && rc-update add bootmisc boot \
    && rc-update add networking boot \
    && (rc-update add syslog boot || true) \
    && rc-update add mount-ro shutdown \
    && rc-update add killprocs shutdown \
    && rc-update add savecache shutdown

# Configure OpenRC for VM environment
RUN sed -i 's/^#rc_sys=.*/rc_sys="lxc"/' /etc/rc.conf \
    && sed -i 's/^#rc_controller_cgroups=.*/rc_controller_cgroups="NO"/' /etc/rc.conf \
    && sed -i 's/^#rc_parallel=.*/rc_parallel="YES"/' /etc/rc.conf

# Static DNS — Docker bind-mounts /etc/resolv.conf during build so we can't
# write to it directly. Store at a stable path and copy at sysinit (before any
# boot-level services need networking).
COPY resolv.conf /etc/resolv.conf.quicksand
RUN printf '#!/sbin/openrc-run\ncommand="/bin/cp"\ncommand_args="/etc/resolv.conf.quicksand /etc/resolv.conf"\n' \
        > /etc/init.d/setup-dns && \
    chmod +x /etc/init.d/setup-dns && \
    rc-update add setup-dns sysinit

# Set hostname
COPY hostname /etc/hostname

# Verify /sbin/init exists (created by busybox-openrc)
RUN test -e /sbin/init || (echo "ERROR: /sbin/init not found" && exit 1)

# Copy the Rust agent binary from builder stage
COPY --from=agent-builder /build/target/release/quicksand-guest-agent /usr/local/bin/quicksand-guest-agent
RUN chmod +x /usr/local/bin/quicksand-guest-agent

# Create OpenRC service for the agent (no supervisor needed)
RUN echo '#!/sbin/openrc-run' > /etc/init.d/quicksand-guest-agent && \
    echo 'command="/usr/local/bin/quicksand-guest-agent"' >> /etc/init.d/quicksand-guest-agent && \
    echo 'command_background="yes"' >> /etc/init.d/quicksand-guest-agent && \
    echo 'pidfile="/run/quicksand-guest-agent.pid"' >> /etc/init.d/quicksand-guest-agent && \
    echo 'output_log="/var/log/quicksand-guest-agent.log"' >> /etc/init.d/quicksand-guest-agent && \
    echo 'error_log="/var/log/quicksand-guest-agent.log"' >> /etc/init.d/quicksand-guest-agent && \
    chmod +x /etc/init.d/quicksand-guest-agent && \
    rc-update add quicksand-guest-agent boot

CMD ["/sbin/init"]
