package org.feuerstein.spitzeisen.codegen;

import java.util.List;
import java.util.stream.Collectors;
import org.feuerstein.spitzeisen.codegen.PythonParameters.Argument;
import software.amazon.smithy.model.traits.DocumentationTrait;
import software.amazon.smithy.model.traits.ExternalDocumentationTrait;

/** Generates public method documentation and examples from the same resolved operation. */
record PythonOperationDocumentation(PythonSettings settings, PythonOperation operation) {
  /**
   * Builds operation documentation using the resolved public parameters.
   *
   * @param arguments public method arguments
   * @param async selected Python surface
   * @return escaped by the destination Python writer
   */
  String render(List<Argument> arguments, boolean async) {
    String docs =
        operation
            .shape
            .getTrait(DocumentationTrait.class)
            .map(DocumentationTrait::getValue)
            .orElse("Call " + operation.method + ".");
    String url =
        operation
            .shape
            .getTrait(ExternalDocumentationTrait.class)
            .map(value -> value.getUrls().values().stream().sorted().findFirst().orElse(""))
            .orElse("");
    String example =
        arguments.stream()
            .filter(argument -> argument.parameter().defaultValue() == null)
            .map(argument -> argument.parameter().name() + "=...")
            .collect(Collectors.joining(", "));
    var text = new StringBuilder(docs).append("\n\n");
    text.append(
        operation.collection
            ? "Fetch every page and validate the returned records."
            : "Fetch and validate the response.");
    if (settings.requireApiRequiredArguments() && !arguments.isEmpty()) {
      text.append(
              "\n\nAPI-required argument typing is enabled. Pass None explicitly to omit a query or header\n")
          .append("when using an evolved API; this may require a type-checker suppression.\n")
          .append("Path labels always need a usable value.");
    }
    if (!url.isEmpty()) {
      text.append("\n\nDocs: ").append(url);
    }
    text.append("\n\nExample::\n\n    ")
        .append(async ? "async with Async" : "with Sync")
        .append(settings.clientName())
        .append("(config) as api:\n        result = ")
        .append(async ? "await " : "")
        .append("api.")
        .append(operation.accessor)
        .append('.')
        .append(operation.method)
        .append('(')
        .append(example)
        .append(")\n");
    if (!arguments.isEmpty() || operation.collection) {
      text.append("\nArgs:\n");
      for (Argument argument : arguments) {
        text.append("    ")
            .append(argument.parameter().name())
            .append(": ")
            .append(
                argument
                    .parameter()
                    .member()
                    .getTrait(DocumentationTrait.class)
                    .map(DocumentationTrait::getValue)
                    .orElse("Value for " + argument.parameter().binding().getLocationName() + "."))
            .append('\n');
      }
      if (operation.collection) {
        text.append("    max_results: Maximum total records (None means no cap).\n")
            .append(
                "    on_validation_error: Override the configured raise/skip validation policy.\n");
      }
    }
    text.append("\nReturns:\n    Validated ")
        .append(operation.collection ? "records" : "response")
        .append(operation.absent ? ", or None for HTTP 404." : ".");
    return text.append(
            "\n\nRaises:\n    pydantic.ValidationError: Invalid response data.\n    ValueError: Invalid input.\n")
        .toString();
  }
}
