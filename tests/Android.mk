LOCAL_BUILD:=false
ifeq ($(TARGET_ARCH),arm)
LOCAL_BUILD:=true
endif
ifeq ($(TARGET_ARCH),mips)
LOCAL_BUILD:=true
endif
ifeq ($LOCAL_BUILD,true)
include $(call all-subdir-makefiles)
endif
