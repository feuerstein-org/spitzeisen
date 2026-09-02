plugins {
    java
    alias(libs.plugins.smithy.base)
}

description = "End-to-end fixture for the Spitzeisen Smithy frontend"

val spitzeisenJavaVersion = rootProject.extra["spitzeisenJavaVersion"] as String

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(spitzeisenJavaVersion.toInt())
    }
}

dependencies {
    implementation(project(":spitzeisen-smithy-codegen"))
}
