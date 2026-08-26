/*
 * dreamfps_layer.c - a Vulkan layer whose entire job is to count frames.
 *
 * WHY THIS EXISTS
 * ---------------
 * Frames per second only exist inside the process drawing them. Nothing
 * in /proc or /sys knows how fast a game is rendering, so a chatbox that
 * wants to print {fps} has to get the number from something living in
 * the game's address space. Until now that something was MangoHud: it
 * logs a CSV, we tailed the CSV. That works, but it makes the feature
 * depend on a second program being installed, configured for logging,
 * and pointed at a folder the user then has to find again.
 *
 * This is the same trick without the detour. The Vulkan loader lets a
 * layer insert itself into every Vulkan application on the system; we
 * insert one that does nothing at all except increment a counter in
 * vkQueuePresentKHR and, twice a second, write the rate into a shared
 * memory page. OSC-DreamChatbox reads that page. No log files, no
 * folder to configure, no launch options.
 *
 * VRChat under Proton renders D3D11 through DXVK, which is Vulkan, so
 * the layer sees it the same way it sees a native Vulkan title.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 * --------------------------------
 * No drawing, no overlay, no swapchain of its own, no extra queue
 * submission, no allocation on the present path. The hot path is one
 * atomic increment and a clock read; everything else happens on the
 * twice-a-second slow path. A layer that hitches the frame rate it is
 * measuring would be worse than useless.
 *
 * It also never enables an extension, never modifies a create-info, and
 * forwards every call it does not implement. An implicit layer sits in
 * front of every Vulkan app the user runs - including ones that have
 * nothing to do with this program - so the only acceptable behaviour is
 * to be invisible.
 *
 * LAYER PLUMBING, BRIEFLY
 * -----------------------
 * Dispatchable Vulkan handles (VkInstance, VkDevice, VkQueue, ...) are
 * pointers whose first word points at a dispatch table. That word is
 * unique per instance/device and is what layers use as a map key. A
 * VkPhysicalDevice shares its VkInstance's key, and a VkQueue shares its
 * VkDevice's key, which is how CreateDevice finds the instance it came
 * from and QueuePresentKHR finds the device it belongs to.
 */

/* Copyright (C) 2026 yakuda */
/* SPDX-License-Identifier: GPL-3.0-or-later */

#define _GNU_SOURCE

#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>

#include "dreamfps_shm.h"

/* Vulkan-Headers dropped this macro in 2023; the loader still expects the
 * handful of symbols below to be visible, and everything else in here is
 * built with -fvisibility=hidden on purpose. */
#ifndef VK_LAYER_EXPORT
#define VK_LAYER_EXPORT __attribute__((visibility("default")))
#endif

#define LAYER_NAME "VK_LAYER_YAKUDA_dreamfps"

/* how long a measuring window is. Half a second is short enough that the
 * number in the chatbox follows the game and long enough that it is not
 * noise: at 90 fps that is 45 frames per sample. */
#define WINDOW_SEC 0.5

/* the first word of any dispatchable handle */
#define DISPATCH_KEY(handle) (*(void **) (handle))

/* ==================================================================== *
 *  the counter and the page it is written to
 * ==================================================================== */

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;

static struct dreamfps_shm *g_map;      /* NULL until the first present */
static char g_shm_name[128];
static int g_shm_failed;                /* do not retry mmap forever */

static unsigned long long g_frames_total;
static unsigned long long g_window_frames;
static double g_window_start;

static double monotonic(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double) ts.tv_sec + (double) ts.tv_nsec / 1e9;
}

static double realtime(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (double) ts.tv_sec + (double) ts.tv_nsec / 1e9;
}

/* /proc/self/comm, so the reader can tell VRChat from a wallpaper
 * engine. Truncated to 15 characters by the kernel, which is enough to
 * recognise a process and not enough to be a privacy problem. */
static void read_comm(char *out, size_t len)
{
    FILE *fh;
    size_t n;

    out[0] = '\0';
    fh = fopen("/proc/self/comm", "re");
    if (!fh)
        return;
    n = fread(out, 1, len - 1, fh);
    fclose(fh);
    out[n] = '\0';
    while (n && (out[n - 1] == '\n' || out[n - 1] == ' '))
        out[--n] = '\0';
}

/* Called with g_lock held. One page per process, named after uid and
 * pid: several games at once each get their own, and a game that
 * crashed leaves a file whose pid the reader can test for life. */
static void shm_setup(void)
{
    int fd;
    void *map;

    if (g_map || g_shm_failed)
        return;
    g_shm_failed = 1;                   /* cleared again on success */

    snprintf(g_shm_name, sizeof g_shm_name, "/dreamfps-%u-%d",
             (unsigned) getuid(), (int) getpid());

    fd = shm_open(g_shm_name, O_CREAT | O_RDWR | O_CLOEXEC, 0600);
    if (fd < 0)
        return;
    if (ftruncate(fd, (off_t) sizeof(struct dreamfps_shm)) != 0) {
        close(fd);
        shm_unlink(g_shm_name);
        return;
    }
    map = mmap(NULL, sizeof(struct dreamfps_shm), PROT_READ | PROT_WRITE,
               MAP_SHARED, fd, 0);
    close(fd);
    if (map == MAP_FAILED)
        return;

    memset(map, 0, sizeof(struct dreamfps_shm));
    g_map = (struct dreamfps_shm *) map;
    g_map->magic = DREAMFPS_MAGIC;
    g_map->version = DREAMFPS_VERSION;
    g_map->pid = (uint32_t) getpid();
    read_comm(g_map->name, DREAMFPS_NAME_LEN);
    g_shm_failed = 0;
}

/* Called with g_lock held. Writes one consistent sample.
 *
 * seq is a seqlock: odd means "being written". The two stores are
 * release-ordered so a reader that sees the new even seq is guaranteed
 * to see the fields written before it. */
static void shm_publish(float fps, float frametime_ms)
{
    uint32_t seq;

    if (!g_map)
        return;
    seq = g_map->seq;
    __atomic_store_n(&g_map->seq, seq + 1, __ATOMIC_RELEASE);

    g_map->fps = fps;
    g_map->frametime_ms = frametime_ms;
    g_map->frames = g_frames_total;
    g_map->updated = realtime();

    __atomic_store_n(&g_map->seq, seq + 2, __ATOMIC_RELEASE);
}

/* The hot path. Everything expensive is behind the window check. */
static void on_present(void)
{
    double now, elapsed;
    float fps, frametime;

    pthread_mutex_lock(&g_lock);

    g_frames_total++;
    g_window_frames++;

    now = monotonic();
    if (g_window_start == 0.0) {
        g_window_start = now;
        pthread_mutex_unlock(&g_lock);
        return;
    }
    elapsed = now - g_window_start;
    if (elapsed < WINDOW_SEC) {
        pthread_mutex_unlock(&g_lock);
        return;
    }

    fps = (float) ((double) g_window_frames / elapsed);
    frametime = fps > 0.0f ? (float) (1000.0 / fps) : 0.0f;
    g_window_frames = 0;
    g_window_start = now;

    shm_setup();                        /* no-op after the first time */
    shm_publish(fps, frametime);

    pthread_mutex_unlock(&g_lock);
}

/* The game is going away. Removing the page here is what keeps /dev/shm
 * from filling up with one file per session ever played; a hard crash
 * skips this, which is why the reader also checks whether the pid is
 * still alive. */
static void shm_teardown(void)
{
    pthread_mutex_lock(&g_lock);
    if (g_map) {
        munmap(g_map, sizeof(struct dreamfps_shm));
        g_map = NULL;
        shm_unlink(g_shm_name);
    }
    g_shm_failed = 0;
    g_window_start = 0.0;
    g_window_frames = 0;
    pthread_mutex_unlock(&g_lock);
}

/* ==================================================================== *
 *  dispatch bookkeeping
 * ==================================================================== */

struct instance_data {
    void *key;
    VkInstance instance;
    PFN_vkGetInstanceProcAddr gipa;
    PFN_vkDestroyInstance DestroyInstance;
    struct instance_data *next;
};

struct device_data {
    void *key;
    PFN_vkGetDeviceProcAddr gdpa;
    PFN_vkDestroyDevice DestroyDevice;
    PFN_vkQueuePresentKHR QueuePresentKHR;
    struct device_data *next;
};

static pthread_mutex_t g_map_lock = PTHREAD_MUTEX_INITIALIZER;
static struct instance_data *g_instances;
static struct device_data *g_devices;

static struct instance_data *instance_get(void *key)
{
    struct instance_data *it;

    pthread_mutex_lock(&g_map_lock);
    for (it = g_instances; it; it = it->next)
        if (it->key == key)
            break;
    pthread_mutex_unlock(&g_map_lock);
    return it;
}

static struct device_data *device_get(void *key)
{
    struct device_data *it;

    pthread_mutex_lock(&g_map_lock);
    for (it = g_devices; it; it = it->next)
        if (it->key == key)
            break;
    pthread_mutex_unlock(&g_map_lock);
    return it;
}

static void device_forget(void *key)
{
    struct device_data **pp, *dead = NULL;
    int empty;

    pthread_mutex_lock(&g_map_lock);
    for (pp = &g_devices; *pp; pp = &(*pp)->next) {
        if ((*pp)->key == key) {
            dead = *pp;
            *pp = dead->next;
            break;
        }
    }
    empty = (g_devices == NULL);
    pthread_mutex_unlock(&g_map_lock);

    free(dead);
    /* the last device of the process is as close to "the game is
     * quitting" as a layer gets to see */
    if (empty)
        shm_teardown();
}

/* ==================================================================== *
 *  chain plumbing
 * ==================================================================== */

static VkLayerInstanceCreateInfo *instance_chain(
        const VkInstanceCreateInfo *info, VkLayerFunction function)
{
    VkLayerInstanceCreateInfo *item = (VkLayerInstanceCreateInfo *) info->pNext;

    while (item && !(item->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO
                     && item->function == function))
        item = (VkLayerInstanceCreateInfo *) item->pNext;
    return item;
}

static VkLayerDeviceCreateInfo *device_chain(
        const VkDeviceCreateInfo *info, VkLayerFunction function)
{
    VkLayerDeviceCreateInfo *item = (VkLayerDeviceCreateInfo *) info->pNext;

    while (item && !(item->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO
                     && item->function == function))
        item = (VkLayerDeviceCreateInfo *) item->pNext;
    return item;
}

/* ==================================================================== *
 *  intercepted entry points
 * ==================================================================== */

static VKAPI_ATTR VkResult VKAPI_CALL dreamfps_CreateInstance(
        const VkInstanceCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator, VkInstance *pInstance)
{
    VkLayerInstanceCreateInfo *link;
    PFN_vkGetInstanceProcAddr gipa;
    PFN_vkCreateInstance create;
    struct instance_data *data;
    VkResult res;

    link = instance_chain(pCreateInfo, VK_LAYER_LINK_INFO);
    if (!link || !link->u.pLayerInfo)
        return VK_ERROR_INITIALIZATION_FAILED;

    gipa = link->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    /* hand the rest of the chain down before calling into it */
    link->u.pLayerInfo = link->u.pLayerInfo->pNext;

    create = (PFN_vkCreateInstance) gipa(NULL, "vkCreateInstance");
    if (!create)
        return VK_ERROR_INITIALIZATION_FAILED;

    res = create(pCreateInfo, pAllocator, pInstance);
    if (res != VK_SUCCESS)
        return res;

    data = calloc(1, sizeof *data);
    if (!data)
        return VK_SUCCESS;              /* we simply will not see this one */
    data->key = DISPATCH_KEY(*pInstance);
    data->instance = *pInstance;
    data->gipa = gipa;
    data->DestroyInstance =
            (PFN_vkDestroyInstance) gipa(*pInstance, "vkDestroyInstance");

    pthread_mutex_lock(&g_map_lock);
    data->next = g_instances;
    g_instances = data;
    pthread_mutex_unlock(&g_map_lock);
    return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL dreamfps_DestroyInstance(
        VkInstance instance, const VkAllocationCallbacks *pAllocator)
{
    struct instance_data **pp, *dead = NULL;
    void *key = DISPATCH_KEY(instance);
    PFN_vkDestroyInstance destroy = NULL;

    pthread_mutex_lock(&g_map_lock);
    for (pp = &g_instances; *pp; pp = &(*pp)->next) {
        if ((*pp)->key == key) {
            dead = *pp;
            *pp = dead->next;
            break;
        }
    }
    pthread_mutex_unlock(&g_map_lock);

    if (dead) {
        destroy = dead->DestroyInstance;
        free(dead);
    }
    if (destroy)
        destroy(instance, pAllocator);
}

static VKAPI_ATTR VkResult VKAPI_CALL dreamfps_CreateDevice(
        VkPhysicalDevice physicalDevice, const VkDeviceCreateInfo *pCreateInfo,
        const VkAllocationCallbacks *pAllocator, VkDevice *pDevice)
{
    VkLayerDeviceCreateInfo *link;
    PFN_vkGetInstanceProcAddr gipa;
    PFN_vkGetDeviceProcAddr gdpa;
    PFN_vkCreateDevice create;
    struct instance_data *inst;
    struct device_data *data;
    VkResult res;

    link = device_chain(pCreateInfo, VK_LAYER_LINK_INFO);
    if (!link || !link->u.pLayerInfo)
        return VK_ERROR_INITIALIZATION_FAILED;

    gipa = link->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    gdpa = link->u.pLayerInfo->pfnNextGetDeviceProcAddr;
    link->u.pLayerInfo = link->u.pLayerInfo->pNext;

    /* a physical device carries its instance's dispatch key */
    inst = instance_get(DISPATCH_KEY(physicalDevice));
    create = (PFN_vkCreateDevice) gipa(inst ? inst->instance : VK_NULL_HANDLE,
                                       "vkCreateDevice");
    if (!create)
        return VK_ERROR_INITIALIZATION_FAILED;

    res = create(physicalDevice, pCreateInfo, pAllocator, pDevice);
    if (res != VK_SUCCESS)
        return res;

    data = calloc(1, sizeof *data);
    if (!data)
        return VK_SUCCESS;
    data->key = DISPATCH_KEY(*pDevice);
    data->gdpa = gdpa;
    data->DestroyDevice =
            (PFN_vkDestroyDevice) gdpa(*pDevice, "vkDestroyDevice");
    /* NULL on a device without a swapchain - a compute job, say. Nothing
     * to measure there, and QueuePresentKHR will never be called. */
    data->QueuePresentKHR =
            (PFN_vkQueuePresentKHR) gdpa(*pDevice, "vkQueuePresentKHR");

    pthread_mutex_lock(&g_map_lock);
    data->next = g_devices;
    g_devices = data;
    pthread_mutex_unlock(&g_map_lock);
    return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL dreamfps_DestroyDevice(
        VkDevice device, const VkAllocationCallbacks *pAllocator)
{
    void *key = DISPATCH_KEY(device);
    struct device_data *data = device_get(key);
    PFN_vkDestroyDevice destroy = data ? data->DestroyDevice : NULL;

    device_forget(key);
    if (destroy)
        destroy(device, pAllocator);
}

static VKAPI_ATTR VkResult VKAPI_CALL dreamfps_QueuePresentKHR(
        VkQueue queue, const VkPresentInfoKHR *pPresentInfo)
{
    /* a queue carries its device's dispatch key */
    struct device_data *data = device_get(DISPATCH_KEY(queue));

    if (!data || !data->QueuePresentKHR)
        return VK_ERROR_INITIALIZATION_FAILED;

    /* Count before forwarding. Present can block on vsync for most of a
     * frame, and counting after it would move every sample by that much.
     * One present call is one frame even when it carries several
     * swapchains: that is one image the user sees, not two. */
    on_present();
    return data->QueuePresentKHR(queue, pPresentInfo);
}

/* ------------------------------------------------------------ queries
 *
 * We add no extensions and no features, so all four of these say "this
 * layer contributes nothing" and otherwise get out of the way. Getting
 * them wrong is how a layer breaks applications it has no business
 * touching, so they are written out rather than left to the loader.
 */

static const VkLayerProperties g_layer_props = {
    .layerName = LAYER_NAME,
    .specVersion = VK_MAKE_VERSION(1, 3, 0),
    .implementationVersion = DREAMFPS_VERSION,
    .description = "OSC-DreamChatbox frame counter",
};

static VKAPI_ATTR VkResult VKAPI_CALL dreamfps_EnumerateInstanceLayerProperties(
        uint32_t *pPropertyCount, VkLayerProperties *pProperties)
{
    if (!pProperties) {
        *pPropertyCount = 1;
        return VK_SUCCESS;
    }
    if (*pPropertyCount < 1) {
        *pPropertyCount = 0;
        return VK_INCOMPLETE;
    }
    *pPropertyCount = 1;
    pProperties[0] = g_layer_props;
    return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL dreamfps_EnumerateDeviceLayerProperties(
        VkPhysicalDevice physicalDevice, uint32_t *pPropertyCount,
        VkLayerProperties *pProperties)
{
    (void) physicalDevice;
    return dreamfps_EnumerateInstanceLayerProperties(pPropertyCount,
                                                     pProperties);
}

static VKAPI_ATTR VkResult VKAPI_CALL
dreamfps_EnumerateInstanceExtensionProperties(
        const char *pLayerName, uint32_t *pPropertyCount,
        VkExtensionProperties *pProperties)
{
    (void) pProperties;
    if (pLayerName && strcmp(pLayerName, LAYER_NAME) == 0) {
        *pPropertyCount = 0;
        return VK_SUCCESS;
    }
    return VK_ERROR_LAYER_NOT_PRESENT;
}

static VKAPI_ATTR VkResult VKAPI_CALL
dreamfps_EnumerateDeviceExtensionProperties(
        VkPhysicalDevice physicalDevice, const char *pLayerName,
        uint32_t *pPropertyCount, VkExtensionProperties *pProperties)
{
    struct instance_data *inst;
    PFN_vkEnumerateDeviceExtensionProperties next;

    if (pLayerName && strcmp(pLayerName, LAYER_NAME) == 0) {
        *pPropertyCount = 0;
        return VK_SUCCESS;
    }
    /* not our question - hand it to the driver */
    inst = instance_get(DISPATCH_KEY(physicalDevice));
    if (!inst)
        return VK_ERROR_INITIALIZATION_FAILED;
    next = (PFN_vkEnumerateDeviceExtensionProperties)
            inst->gipa(inst->instance, "vkEnumerateDeviceExtensionProperties");
    if (!next)
        return VK_ERROR_INITIALIZATION_FAILED;
    return next(physicalDevice, pLayerName, pPropertyCount, pProperties);
}

/* ==================================================================== *
 *  the two proc-addr functions and the loader handshake
 * ==================================================================== */

#define ENTRY(name) \
    if (strcmp(pName, "vk" #name) == 0) return (PFN_vkVoidFunction) dreamfps_##name

VK_LAYER_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL
dreamfps_GetDeviceProcAddr(VkDevice device, const char *pName)
{
    struct device_data *data;

    ENTRY(GetDeviceProcAddr);
    ENTRY(DestroyDevice);
    ENTRY(QueuePresentKHR);

    data = device ? device_get(DISPATCH_KEY(device)) : NULL;
    if (!data || !data->gdpa)
        return NULL;
    return data->gdpa(device, pName);
}

VK_LAYER_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL
dreamfps_GetInstanceProcAddr(VkInstance instance, const char *pName)
{
    struct instance_data *data;

    ENTRY(GetInstanceProcAddr);
    ENTRY(CreateInstance);
    ENTRY(DestroyInstance);
    ENTRY(CreateDevice);
    ENTRY(EnumerateInstanceLayerProperties);
    ENTRY(EnumerateInstanceExtensionProperties);
    ENTRY(EnumerateDeviceLayerProperties);
    ENTRY(EnumerateDeviceExtensionProperties);
    /* device-level names are asked for through here too */
    ENTRY(GetDeviceProcAddr);
    ENTRY(DestroyDevice);
    ENTRY(QueuePresentKHR);

    data = instance ? instance_get(DISPATCH_KEY(instance)) : NULL;
    if (!data || !data->gipa)
        return NULL;
    return data->gipa(instance, pName);
}

#undef ENTRY

/* The modern handshake: the loader asks which interface version we speak
 * and takes our two proc-addr functions from the struct. Version 2 is
 * what everything since 2017 uses. */
VK_LAYER_EXPORT VKAPI_ATTR VkResult VKAPI_CALL
vkNegotiateLoaderLayerInterfaceVersion(VkNegotiateLayerInterface *pVersionStruct)
{
    if (!pVersionStruct
            || pVersionStruct->sType != LAYER_NEGOTIATE_INTERFACE_STRUCT)
        return VK_ERROR_INITIALIZATION_FAILED;

    if (pVersionStruct->loaderLayerInterfaceVersion > 2)
        pVersionStruct->loaderLayerInterfaceVersion = 2;

    pVersionStruct->pfnGetInstanceProcAddr = dreamfps_GetInstanceProcAddr;
    pVersionStruct->pfnGetDeviceProcAddr = dreamfps_GetDeviceProcAddr;
    pVersionStruct->pfnGetPhysicalDeviceProcAddr = NULL;
    return VK_SUCCESS;
}

/* Kept for loaders old enough to look the entry points up by name
 * instead of negotiating. Costs two symbols. */
VK_LAYER_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL
vkGetInstanceProcAddr(VkInstance instance, const char *pName)
{
    return dreamfps_GetInstanceProcAddr(instance, pName);
}

VK_LAYER_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL
vkGetDeviceProcAddr(VkDevice device, const char *pName)
{
    return dreamfps_GetDeviceProcAddr(device, pName);
}

/* Last resort: a game killed with SIGKILL never reaches DestroyDevice,
 * but a normal exit does reach here. */
__attribute__((destructor))
static void dreamfps_fini(void)
{
    shm_teardown();
}
