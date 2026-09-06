package org.feuerstein.spitzeisen.codegen;

import java.util.Collection;
import java.util.List;
import java.util.Set;

/** Generates the aggregate client and package markers from validated operations. */
record PythonClientGenerator(
    PythonSettings settings,
    Collection<PythonOperation> operations,
    PythonWriterDelegator writers) {
  /** Writes both public client surfaces using Smithy's managed writers. */
  void generate() {
    for (boolean async : List.of(true, false)) {
      String folder = async ? "_async" : "_sync";
      String prefix = async ? "Async" : "Sync";
      writers.packageMarker(
          folder + "/__init__.py", "\"\"\"Public " + prefix + " API surface.\"\"\"\n", true);
      writers.packageMarker(
          folder + "/_generated/__init__.py",
          "\"\"\"Generated implementation. Do not edit.\"\"\"\n",
          false);
      writers.useSdkWriter(
          folder + "/_generated/client.py", false, writer -> client(writer, async));
      writers.extension(
          folder + "/client.py",
          settings.packageName() + "." + folder + "._generated.client",
          prefix + settings.clientName() + "Base",
          prefix + settings.clientName());
    }
  }

  private void client(PythonWriter writer, boolean async) {
    String prefix = async ? "Async" : "Sync";
    String folder = async ? "_async" : "_sync";
    String name = prefix + settings.clientName() + "Base";
    writer.reserve(Set.of(name, "config", "self", "args", "operation_api"));
    writer.write("class $L:", name).indent();
    writer.docs(
        "Client for " + settings.vendor() + ". Shares one configuration across operation APIs.");
    writer
        .write(
            "def __init__(self, config: $T) -> None:",
            PythonSymbols.symbol("spitzeisen", prefix + "SpitzeisenConfig").toBuilder()
                .putProperty("runtimeAnnotation", true)
                .build())
        .indent();
    writer.docs("Build all operation APIs around the supplied configuration.");
    writer.write("self.config = config");
    for (var operation : operations) {
      writer.write(
          "self.$L = $T(config)",
          operation.accessor,
          PythonSymbols.symbol(
              settings.packageName() + "." + folder + "." + operation.key,
              prefix + operation.className));
    }
    writer.write("self._operation_apis = (").indent();
    operations.forEach(operation -> writer.write("self.$L,", operation.accessor));
    writer.dedent().write(")").dedent();
    String a = async ? "a" : "";
    writer
        .write(
            "\n$Ldef __$Lenter__(self) -> $T:",
            async ? "async " : "",
            a,
            PythonSymbols.symbol("typing", "Self"))
        .indent();
    writer.docs("Enter each API while sharing one lazily-created HTTP client.");
    writer.write("for operation_api in self._operation_apis:").indent();
    writer.write("$Loperation_api.__$Lenter__()", async ? "await " : "", a).dedent();
    writer.write("return self").dedent();
    writer
        .write("\n$Ldef __$Lexit__(self, *args: object) -> None:", async ? "async " : "", a)
        .indent();
    writer.docs("Release each API and close the shared owned client after its last holder.");
    writer.write("for operation_api in reversed(self._operation_apis):").indent();
    writer
        .write("$Loperation_api.__$Lexit__(*args)", async ? "await " : "", a)
        .dedent()
        .dedent()
        .dedent();
  }
}
