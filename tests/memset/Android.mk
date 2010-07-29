# Copyright 2006 The Android Open Source Project

ifeq ($(TARGET_ARCH),mips)

LOCAL_PATH:= $(call my-dir)
include $(CLEAR_VARS)

LOCAL_SRC_FILES:= test_memset.c oldmemset.S

LOCAL_MODULE:= test_memset

LOCAL_FORCE_STATIC_EXECUTABLE := true
LOCAL_STATIC_LIBRARIES := libcutils libc
LOCAL_SHARED_LIBRARIES := libcutils libc 
LOCAL_MODULE_TAGS := optional

include $(BUILD_EXECUTABLE)

endif
