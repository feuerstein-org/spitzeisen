package org.feuerstein.spitzeisen.codegen;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.util.ServiceLoader;
import org.junit.jupiter.api.Test;
import software.amazon.smithy.build.SmithyBuildPlugin;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.node.Node;

final class ClientPlanCompilerTest {
    @Test
    void compilesTheRendererContractFromSmithySemantics() throws IOException, URISyntaxException {
        var modelSource = resource("/native-weather.smithy");
        var model = Model.assembler()
                .discoverModels()
                .addImport(modelSource)
                .assemble()
                .unwrap();
        var settings = SpitzeisenSettings.fromNode(Node.parse("""
                {
                    "service": "native.weather#WeatherService",
                    "package": "native_weather_sdk",
                    "clientName": "NativeWeatherApi"
                }
                """).expectObjectNode());

        var actual = new ClientPlanCompiler(model, settings).compile();
        var expected = Node.parse(readResource("/native-weather-plan.json"));

        Node.assertEquals(expected, actual.node());
        assertEquals(1, actual.responseShapes().size());
        assertTrue(actual.responseShapes().stream().anyMatch(id -> id.toString().equals("native.weather#Weather")));
    }

    @Test
    void pluginIsDiscoveredThroughJavaSpi() {
        var plugins = ServiceLoader.load(SmithyBuildPlugin.class);

        assertTrue(plugins.stream().anyMatch(provider -> provider.type().equals(SpitzeisenPythonClientCodegenPlugin.class)));
    }

    private static java.net.URL resource(String name) {
        var resource = ClientPlanCompilerTest.class.getResource(name);
        if (resource == null) {
            throw new IllegalArgumentException("missing test resource " + name);
        }
        return resource;
    }

    private static String readResource(String name) throws IOException, URISyntaxException {
        return java.nio.file.Files.readString(java.nio.file.Path.of(resource(name).toURI()), StandardCharsets.UTF_8);
    }
}
