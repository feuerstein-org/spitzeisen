import java.util.Properties

val toolchainProperties = Properties().apply {
    rootProject.file("../src/spitzeisen/codegen/smithy/toolchain.properties").inputStream().use(::load)
}

extra["spitzeisenJavaVersion"] = toolchainProperties.getProperty("java.version")
extra["spitzeisenSmithyVersion"] = toolchainProperties.getProperty("smithy.version")
extra["spitzeisenAlloyVersion"] = toolchainProperties.getProperty("alloy.version")

allprojects {
    group = "org.feuerstein.spitzeisen"
    version = "0.1.0-SNAPSHOT"

    repositories {
        mavenCentral()
    }

    dependencyLocking {
        lockAllConfigurations()
    }
}
