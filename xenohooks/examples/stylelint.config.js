/**
 * Example Stylelint configuration for xenohooks
 *
 * This configuration balances code quality with practical CSS patterns.
 * Key adjustment: selector-max-class raised to 5 to support common patterns.
 */

module.exports = {
  extends: ['stylelint-config-standard'],

  rules: {
    /**
     * Selector max class: Keep at 3 but exclude state/modifier classes
     *
     * Rationale: State classes like .active, .disabled, etc. are modifiers
     * that shouldn't count toward specificity limits. This allows patterns like:
     *
     * ✅ .nav-item.active .badge.alert (4 classes, but .active and .alert ignored)
     * ✅ .button.primary.disabled (3 classes, but .disabled ignored)
     * ✅ .card.expanded .header.loading (4 classes, but .expanded and .loading ignored)
     *
     * Still blocks genuinely over-specific selectors:
     * ❌ .component.variant.size.theme (4+ non-state classes)
     */
    'selector-max-class': [3, {
      ignoreClasses: [
        // State classes
        'active', 'inactive', 'disabled', 'enabled', 'selected', 'unselected',
        'checked', 'unchecked', 'valid', 'invalid', 'required', 'optional',
        'hover', 'focus', 'visited', 'loading', 'pending', 'complete',
        'open', 'closed', 'expanded', 'collapsed', 'hidden', 'visible',
        'locked', 'unlocked', 'readonly', 'editable',

        // Status/notification classes
        'error', 'success', 'warning', 'info', 'alert',

        // Common utility classes
        'first', 'last', 'even', 'odd'
      ]
    }],

    /**
     * Other reasonable Stylelint rules for hook integration
     */
    'selector-max-id': 0,  // Disallow IDs in selectors (use classes)
    'selector-max-specificity': '0,4,1',  // Reasonable specificity limit
    'selector-max-compound-selectors': 4,  // Limit nesting depth
    'declaration-no-important': true,  // Warn on !important usage

    // Allow CSS custom properties (design tokens)
    'custom-property-pattern': '^[a-z][a-zA-Z0-9-]*$',

    // Modern CSS features
    'at-rule-no-unknown': [true, {
      ignoreAtRules: ['tailwind', 'apply', 'layer', 'config']
    }]
  }
};