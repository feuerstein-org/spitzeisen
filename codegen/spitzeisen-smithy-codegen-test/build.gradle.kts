plugins {
    java
    alias(libs.plugins.smithy.base)
}

description = "End-to-end fixture for the Spitzeisen Smithy frontend"

dependencies {
    implementation(project(":spitzeisen-smithy-codegen"))
}
