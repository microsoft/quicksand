# quicksand-base-scaffold base image
# See quicksand-ubuntu/docker/Dockerfile for a full example.

# Stage 1: Build the Rust guest agent
FROM rust:bookworm AS agent-builder
WORKDIR /build
COPY agent/Cargo.toml agent/Cargo.lock* ./
COPY agent/src ./src
RUN cargo build --release && strip /build/target/release/quicksand-guest-agent

# Stage 2: Your base image
FROM ubuntu:24.04

# Non-interactive apt
ENV DEBIAN_FRONTEND=noninteractive

# TODO: Install your kernel and init system
# RUN apt-get update && apt-get install -y linux-image-virtual systemd systemd-sysv

# TODO: 9p modules for host file sharing
# RUN echo "9p" >> /etc/modules && echo "9pnet" >> /etc/modules && echo "9pnet_virtio" >> /etc/modules

# TODO: Create quicksand user with passwordless sudo
# RUN useradd -m -s /bin/bash quicksand

# TODO: DHCP networking

# Install the guest agent
COPY --from=agent-builder /build/target/release/quicksand-guest-agent /usr/local/bin/quicksand-guest-agent

CMD ["/sbin/init"]
