package org.feuerstein.spitzeisen.codegen;

import java.util.HashSet;
import java.util.Set;
import software.amazon.smithy.build.PluginContext;
import software.amazon.smithy.build.SmithyBuildPlugin;
import software.amazon.smithy.jsonschema.JsonSchemaConfig;
import software.amazon.smithy.jsonschema.JsonSchemaConverter;
import software.amazon.smithy.jsonschema.JsonSchemaVersion;
import software.amazon.smithy.model.neighbor.Walker;
import software.amazon.smithy.model.shapes.ShapeId;

/** Smithy semantic frontend for Spitzeisen's Python client generator. */
public final class SpitzeisenPythonClientCodegenPlugin implements SmithyBuildPlugin {
    public static final String NAME = "spitzeisen-python-client-codegen";

    @Override
    public String getName() {
        return NAME;
    }

    @Override
    public void execute(PluginContext context) {
        var settings = SpitzeisenSettings.fromNode(context.getSettings());
        var compiled = new ClientPlanCompiler(context.getModel(), settings).compile();
        context.getFileManifest().writeJson("client-plan.json", compiled.node());
        context.getFileManifest().writeJson(
                "model-schema.json",
                createModelSchema(context, compiled.responseShapes())
        );
    }

    private static software.amazon.smithy.model.node.Node createModelSchema(
            PluginContext context,
            Set<ShapeId> responseShapes
    ) {
        var model = context.getModel();
        var included = new HashSet<ShapeId>();
        var walker = new Walker(model);
        for (var root : responseShapes) {
            walker.walkShapes(model.expectShape(root)).forEach(shape -> included.add(shape.getId()));
        }

        var config = new JsonSchemaConfig();
        config.setService(resolveService(model, context.getSettings()));
        config.setJsonSchemaVersion(JsonSchemaVersion.DRAFT2020_12);
        config.setUseIntegerType(true);
        config.setUseJsonName(true);
        config.setAddReferenceDescriptions(true);

        return JsonSchemaConverter.builder()
                .model(model)
                .config(config)
                .shapePredicate(shape -> included.contains(shape.getId()))
                .build()
                .convert()
                .toNode();
    }

    private static ShapeId resolveService(
            software.amazon.smithy.model.Model model,
            software.amazon.smithy.model.node.ObjectNode settingsNode
    ) {
        return SpitzeisenSettings.fromNode(settingsNode).service().orElseGet(() -> {
            if (model.getServiceShapes().size() != 1) {
                throw new IllegalArgumentException("service is required when the model does not contain exactly one service");
            }
            return model.getServiceShapes().iterator().next().getId();
        });
    }
}
