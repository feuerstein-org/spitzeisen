package org.feuerstein.spitzeisen.codegen;

import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.knowledge.NullableIndex;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.shapes.MemberShape;
import software.amazon.smithy.model.traits.ClientOptionalTrait;
import software.amazon.smithy.model.traits.DefaultTrait;

/** Shared Smithy presence rules for Python signatures and response schemas. */
final class MemberPresence {
  private MemberPresence() {}

  /**
   * Checks Smithy's non-authoritative client nullability.
   *
   * @param model assembled model
   * @param member member being projected
   * @return whether clients may omit or receive null for the member
   */
  static boolean clientNullable(Model model, MemberShape member) {
    return NullableIndex.of(model).isMemberNullable(member, NullableIndex.CheckMode.CLIENT);
  }

  /**
   * Selects input typing policy while preserving explicit clientOptional.
   *
   * @param model assembled model
   * @param member operation input member
   * @param settings target policies
   * @return whether the public input annotation includes None
   */
  static boolean inputNullable(Model model, MemberShape member, PythonSettings settings) {
    return member.hasTrait(ClientOptionalTrait.class)
        || NullableIndex.of(model)
            .isMemberNullable(
                member,
                settings.requireApiRequiredArguments()
                    ? NullableIndex.CheckMode.SERVER
                    : NullableIndex.CheckMode.CLIENT);
  }

  /**
   * Resolves defaults, with explicit clientOptional suppressing member and target defaults.
   *
   * @param model assembled model
   * @param member member being projected
   * @return modeled default or Java null when no client default applies
   */
  static Node modeledDefault(Model model, MemberShape member) {
    return member.hasTrait(ClientOptionalTrait.class)
        ? null
        : member
            .getMemberTrait(model, DefaultTrait.class)
            .map(DefaultTrait::toNode)
            .filter(value -> !value.isNullNode())
            .orElse(null);
  }

  /**
   * Resolves input defaults without allowing custom defaults to contradict presence rules.
   *
   * @param model assembled model
   * @param member operation input member
   * @param settings target policies
   * @return Python default value, or Java null for a required keyword
   * @throws IllegalArgumentException if a custom default contradicts the member's presence rules
   */
  static Node inputDefault(Model model, MemberShape member, PythonSettings settings) {
    var configured = SdkPolicy.of(member, "clientDefault");
    Node value = modeledDefault(model, member);
    if (!configured.isEmpty()) {
      if (member.hasTrait(ClientOptionalTrait.class)) {
        throw new IllegalArgumentException(
            "clientDefault conflicts with @clientOptional: " + member.getId());
      }
      value = configured.expectMember("value");
      if (value.isNullNode() && !inputNullable(model, member, settings)) {
        throw new IllegalArgumentException(
            "null clientDefault conflicts with a non-nullable input: " + member.getId());
      }
    }
    return value != null ? value : inputNullable(model, member, settings) ? Node.nullNode() : null;
  }
}
