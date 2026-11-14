cmake_minimum_required(VERSION 3.16)
project(ModViewerDecoder CXX)
set(CMAKE_CXX_STANDARD 11)
add_library(CUE4Parse-Natives SHARED
    "${PARSER}/CUE4Parse-Natives/Features.cpp"
    "${PARSER}/CUE4Parse-Natives/ACL/ACL.cpp")
set_target_properties(CUE4Parse-Natives PROPERTIES PREFIX "")
target_compile_definitions(CUE4Parse-Natives PRIVATE WITH_ACL WIN_EXPORT)
target_include_directories(CUE4Parse-Natives PRIVATE
    "${PARSER}/CUE4Parse-Natives/common"
    "${ACL}/includes" "${RTM}/includes")
if(MINGW)
    target_link_options(CUE4Parse-Natives PRIVATE -static -static-libgcc -static-libstdc++)
endif()
