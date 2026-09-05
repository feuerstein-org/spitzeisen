plugins {
    `java-library`
    checkstyle
    alias(libs.plugins.smithy.jar)
    alias(libs.plugins.spotless)
}

description = "Smithy Python client generator for Spitzeisen"

val spitzeisenJavaVersion = rootProject.extra["spitzeisenJavaVersion"] as String
val spitzeisenSmithyVersion = rootProject.extra["spitzeisenSmithyVersion"] as String

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(spitzeisenJavaVersion.toInt())
    }
}

dependencies {
    implementation("software.amazon.smithy:smithy-model:$spitzeisenSmithyVersion")
    implementation("software.amazon.smithy:smithy-build:$spitzeisenSmithyVersion")
    implementation("software.amazon.smithy:smithy-codegen-core:$spitzeisenSmithyVersion")
    implementation("software.amazon.smithy:smithy-jsonschema:$spitzeisenSmithyVersion")

    testImplementation(platform(libs.junit.bom))
    testImplementation(libs.junit.jupiter)
    testRuntimeOnly(libs.junit.platform.launcher)
}

tasks.withType<JavaCompile>().configureEach {
    options.encoding = "UTF-8"
    options.release = spitzeisenJavaVersion.toInt()
    options.compilerArgs.addAll(listOf("-Xlint:all", "-Werror"))
}

checkstyle {
    toolVersion = libs.versions.checkstyle.get()
}

tasks.withType<Checkstyle>().configureEach {
    configFile = rootProject.file("config/checkstyle/checkstyle.xml")
    maxWarnings = 0
}

// Tests benefit from formatting, but their method names describe test cases rather than an API.
tasks.named<Checkstyle>("checkstyleTest") {
    enabled = false
}

spotless {
    java {
        googleJavaFormat(libs.versions.google.java.format.get())
    }
}

tasks.named("check") {
    dependsOn("spotlessCheck")
}

tasks.withType<Test>().configureEach {
    useJUnitPlatform()
}

tasks.jar {
    archiveFileName = "spitzeisen-python-codegen.jar"
    isPreserveFileTimestamps = false
    isReproducibleFileOrder = true
    // The Smithy JAR plugin adds host- and time-specific values immediately before packaging.
    // Register this action first so its later `doFirst` action runs before this normalization.
    doFirst("normalizeReproducibleManifest") {
        manifest.attributes(
            "Build-Timestamp" to "1980-02-01T00:00:00Z",
            "Build-Jdk" to spitzeisenJavaVersion,
            "Build-OS" to "reproducible",
        )
    }
}

val installRuntimeJar = tasks.register<Copy>("installRuntimeJar") {
    group = "distribution"
    description = "Install the reproducible Smithy plugin JAR into the Python package"
    dependsOn(tasks.jar)
    from(tasks.jar.flatMap { it.archiveFile })
    into(rootProject.file("../src/spitzeisen/codegen/smithy"))
}

smithy {
    smithyBuildConfigs.set(files())
}
