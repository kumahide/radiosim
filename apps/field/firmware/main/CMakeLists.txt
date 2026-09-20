idf_component_register(
    SRCS "main.c" "settings.c" "link.c" "radio.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_wifi esp_timer nvs_flash esp_driver_gpio esp_driver_usb_serial_jtag)

# ファームの版を**ビルドのたびに** git から取り直す（B-256）。出力の無いカスタム
# ターゲットは常に古い扱い＝毎回走る。ヘッダは中身が変わったときだけ書き換わる。
# 刻印の前に、sdkconfig が sdkconfig.defaults どおりかを照合する（B-260）＝
# 版はコミットしか表さないので、古い sdkconfig で焼くと中身と版が食い違う。
set(RADIOSIM_FIELD_VERSION_HEADER "${CMAKE_CURRENT_BINARY_DIR}/radiosim_field_version.h")
idf_build_get_property(RADIOSIM_FIELD_SDKCONFIG SDKCONFIG)
add_custom_target(radiosim_field_version ALL
    COMMAND ${CMAKE_COMMAND}
        -DDEFAULTS=${CMAKE_CURRENT_LIST_DIR}/../sdkconfig.defaults
        -DSDKCONFIG=${RADIOSIM_FIELD_SDKCONFIG}
        -P ${CMAKE_CURRENT_LIST_DIR}/../sdkconfig_check.cmake
    COMMAND ${CMAKE_COMMAND}
        -DSRC_DIR=${CMAKE_CURRENT_LIST_DIR}
        -DOUT=${RADIOSIM_FIELD_VERSION_HEADER}
        -P ${CMAKE_CURRENT_LIST_DIR}/../version.cmake
    BYPRODUCTS ${RADIOSIM_FIELD_VERSION_HEADER}
    VERBATIM)
add_dependencies(${COMPONENT_LIB} radiosim_field_version)
target_include_directories(${COMPONENT_LIB} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
