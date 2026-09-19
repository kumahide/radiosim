idf_component_register(
    SRCS "main.c" "settings.c" "link.c" "radio.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_wifi esp_timer nvs_flash esp_driver_gpio esp_driver_usb_serial_jtag
                  esp_app_format)
