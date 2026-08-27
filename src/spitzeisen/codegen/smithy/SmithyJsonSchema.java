import java.nio.file.Path;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import software.amazon.smithy.jsonschema.JsonSchemaConfig;
import software.amazon.smithy.jsonschema.JsonSchemaConverter;
import software.amazon.smithy.jsonschema.JsonSchemaVersion;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.neighbor.Walker;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.shapes.Shape;
import software.amazon.smithy.model.shapes.ShapeId;

/** Minimal launcher for Smithy's official JSON Schema converter. */
public final class SmithyJsonSchema {
    private SmithyJsonSchema() {}

    public static void main(String[] args) {
        if (args.length < 3) {
            throw new IllegalArgumentException("expected MODEL SERVICE RESPONSE_SHAPE...");
        }
        Model model = Model.assembler()
                .addImport(Path.of(args[0]))
                .assemble()
                .unwrap();
        ShapeId service = ShapeId.from(args[1]);
        Set<ShapeId> included = new HashSet<>();
        Walker walker = new Walker(model);
        Arrays.stream(args).skip(2).map(ShapeId::from).forEach(root -> {
            Shape shape = model.expectShape(root);
            walker.walkShapes(shape).forEach(item -> included.add(item.getId()));
        });

        JsonSchemaConfig config = new JsonSchemaConfig();
        config.setService(service);
        config.setJsonSchemaVersion(JsonSchemaVersion.DRAFT2020_12);
        config.setUseIntegerType(true);
        config.setUseJsonName(true);
        config.setAddReferenceDescriptions(true);

        var document = JsonSchemaConverter.builder()
                .model(model)
                .config(config)
                .shapePredicate(shape -> included.contains(shape.getId()))
                .build()
                .convert();
        System.out.println(Node.prettyPrintJson(document.toNode()));
    }
}
