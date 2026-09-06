package org.feuerstein.spitzeisen.codegen;

import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.function.Consumer;
import software.amazon.smithy.build.FileManifest;
import software.amazon.smithy.codegen.core.SymbolProvider;
import software.amazon.smithy.codegen.core.WriterDelegator;
import software.amazon.smithy.model.node.ObjectNode;

/** Smithy-managed Python writers with the ownership metadata needed by SDK finalization. */
final class PythonWriterDelegator extends WriterDelegator<PythonWriter> {
  private final Map<String, Boolean> files = new TreeMap<>();

  /**
   * Creates lazily allocated writers in the plugin's isolated output directory.
   *
   * @param manifest Smithy build output
   * @param symbols model symbols
   */
  PythonWriterDelegator(FileManifest manifest, SymbolProvider symbols) {
    super(manifest, symbols, (filename, namespace) -> new PythonWriter(Set.of()));
  }

  /**
   * Writes one SDK module, retaining its public customization policy.
   *
   * @param path SDK-relative file name
   * @param createOnce whether finalization preserves an existing public module
   * @param generator source generator
   * @throws IllegalArgumentException if two generators own the same file
   */
  void useSdkWriter(String path, boolean createOnce, Consumer<PythonWriter> generator) {
    if (files.putIfAbsent(path, createOnce) != null) {
      throw new IllegalArgumentException("duplicate generated path: " + path);
    }
    useFileWriter("sdk/" + path, generator);
  }

  /**
   * Writes a minimal package marker without a generated-module preamble.
   *
   * @param path SDK-relative package marker
   * @param source package documentation and exports
   * @param createOnce whether finalization preserves an existing marker
   */
  void packageMarker(String path, String source, boolean createOnce) {
    useSdkWriter(
        path,
        createOnce,
        writer -> {
          writer.omitPreamble();
          writer.write("$L", source.stripTrailing());
        });
  }

  /**
   * Writes a public subclass that SDK users can customize.
   *
   * @param path SDK-relative file name
   * @param module module exporting the base class
   * @param base base class name
   * @param name public class name
   */
  void extension(String path, String module, String base, String name) {
    useSdkWriter(
        path,
        true,
        writer -> {
          writer.reserve(Set.of(name));
          writer
              .write(
                  "# Created once. Safe to customize.\nclass $L($T):",
                  name,
                  PythonSymbols.symbol(module, base))
              .indent();
          writer.docs("Public " + name + " extension point.");
          writer.dedent();
        });
  }

  /**
   * Collects ownership metadata for all generated SDK modules.
   *
   * @return deterministic ownership flags for the Python build manifest
   */
  ObjectNode files() {
    var result = ObjectNode.builder();
    files.forEach(result::withMember);
    return result.build();
  }
}
