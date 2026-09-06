package org.feuerstein.spitzeisen.codegen;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/** Allocates target identifiers within independent Python lexical scopes. */
final class PythonNames {
  private final Map<String, String> allocated = new HashMap<>();
  private final Map<String, Set<String>> scopes = new HashMap<>();

  /** Creates the SDK's operation and parameter naming scopes. */
  PythonNames() {
    reserve("modules", Set.of("client", "__init__", "_generated"));
    reserve(
        "methods",
        Set.of(
            "__init__",
            "__enter__",
            "__exit__",
            "__aenter__",
            "__aexit__",
            "request",
            "_request",
            "_request_optional",
            "_http_client",
            "_paginate",
            "_get_all_pages",
            "_get_all_pages_optional",
            "_validate_input",
            "_validate_response",
            "_response_validation_context",
            "_validate_records",
            "_resolve_validation_mode"));
    reserve(
        "accessors",
        Set.of(
            "config",
            "_operation_apis",
            "close",
            "__enter__",
            "__exit__",
            "__aenter__",
            "__aexit__"));
  }

  /**
   * Allocates deterministic identifiers in a lexical scope.
   *
   * @return resolved Python source or identifier
   * @param scope lexical allocation scope
   * @param key stable identity within the scope
   * @param preferred preferred Python name
   */
  String allocate(String scope, String key, String preferred) {
    return allocated.computeIfAbsent(
        scope + ":" + key,
        ignored -> {
          var used = scopes.computeIfAbsent(scope, unused -> new HashSet<>());
          String name = preferred;
          for (int suffix = 2; used.contains(name); suffix++) {
            name = preferred + "_" + suffix;
          }
          used.add(name);
          return name;
        });
  }

  /**
   * Reserves runtime and synthesized names before allocating modeled names.
   *
   * @param scope lexical allocation scope
   * @param names identifiers reserved by generated code
   */
  void reserve(String scope, Set<String> names) {
    scopes.computeIfAbsent(scope, ignored -> new HashSet<>()).addAll(names);
  }
}
