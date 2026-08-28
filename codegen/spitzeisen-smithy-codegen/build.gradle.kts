plugins {
    `java-library`
}

description = "Smithy semantic frontend for Spitzeisen's Python SDK generator"

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(17)
    }
}

dependencies {
    api(libs.smithy.codegen)
    implementation(libs.smithy.build)
    implementation(libs.smithy.jsonschema)

    testImplementation(platform(libs.junit.bom))
    testImplementation(libs.junit.jupiter)
    testRuntimeOnly(libs.junit.platform.launcher)
}

tasks.withType<JavaCompile>().configureEach {
    options.encoding = "UTF-8"
    options.release = 17
    options.compilerArgs.addAll(listOf("-Xlint:all", "-Werror"))
}

tasks.withType<Test>().configureEach {
    useJUnitPlatform()
}

tasks.jar {
    archiveFileName = "spitzeisen-codegen.jar"
    isPreserveFileTimestamps = false
    isReproducibleFileOrder = true
}

val installRuntimeJar by tasks.registering(Copy::class) {
    group = "distribution"
    description = "Install the reproducible Smithy plugin JAR into the Python package"
    dependsOn(tasks.jar)
    from(tasks.jar.flatMap { it.archiveFile })
    into(rootProject.file("../src/spitzeisen/codegen/smithy"))
}

tasks.processResources {
    from(rootProject.file("../src/spitzeisen/codegen/smithy/spitzeisen.smithy")) {
        into("META-INF/smithy")
    }
}
