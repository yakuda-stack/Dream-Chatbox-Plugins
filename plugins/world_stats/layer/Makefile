# Makefile - builds the dreamfps Vulkan layer for the World Stats plugin.
#
# Different from the one that used to live in the app's packaging folder
# in one way that matters: the .so is built right here, next to the
# plugin's Python files, because that is where fpslayer.py looks first.
# Uninstall the plugin and the layer goes with it.
#
#   make          the layer
#   make check    compile-only pass with -Werror
#   make clean
#
# Needs a C compiler and the Vulkan headers:
#   Arch    pacman -S base-devel vulkan-headers
#   Fedora  dnf install gcc make vulkan-headers
#   Debian  apt install build-essential libvulkan-dev
#
# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

CC      ?= gcc
CFLAGS  ?= -O2 -g
CFLAGS  += -std=c11 -Wall -Wextra -fPIC -fvisibility=hidden -pthread
LDFLAGS += -shared -pthread
LDLIBS  += -lrt

SRC := dreamfps_layer.c
HDR := dreamfps_shm.h
LIB := libVkLayer_dreamfps.so

.PHONY: all check clean

all: $(LIB)

$(LIB): $(SRC) $(HDR)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(SRC) $(LDLIBS)

check:
	$(CC) $(CFLAGS) -Werror -fsyntax-only $(SRC)
	@echo "dreamfps: syntax clean"

clean:
	rm -f $(LIB)
