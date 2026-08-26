// Ace's stock INI mode carries an unterminated quote into every following
// line. 3DMigoto INIs commonly use apostrophes and quotes as ordinary value
// characters, so strings must always end at the authored row boundary.

export function registerIniHighlightMode() {
  window.ace.define('ace/mode/mod_viewer_ini_highlight_rules',
    ['require', 'exports', 'module', 'ace/lib/oop', 'ace/mode/text_highlight_rules'],
    (require, exports) => {
      const oop = require('ace/lib/oop');
      const TextHighlightRules = require('ace/mode/text_highlight_rules').TextHighlightRules;

      const IniHighlightRules = function () {
        this.$rules = { start: [
          { token: 'comment.line.number-sign.ini', regex: '#.*' },
          { token: 'comment.line.semicolon.ini', regex: ';.*' },
          {
            token: ['keyword.other.definition.ini', 'text',
              'punctuation.separator.key-value.ini'],
            regex: '\\b([a-zA-Z0-9_.-]+)\\b(\\s*)(=)',
          },
          {
            token: ['punctuation.definition.entity.ini',
              'constant.section.group-title.ini',
              'punctuation.definition.entity.ini'],
            regex: '^(\\[)(.*?)(\\])',
          },
          { token: 'string.quoted.single.ini', regex: "'(?:\\\\.|[^'])*(?:'|$)" },
          { token: 'string.quoted.double.ini', regex: '"(?:\\\\.|[^"])*(?:"|$)' },
        ] };
        this.normalizeRules();
      };
      oop.inherits(IniHighlightRules, TextHighlightRules);
      exports.IniHighlightRules = IniHighlightRules;
    });

  window.ace.define('ace/mode/mod_viewer_ini',
    ['require', 'exports', 'module', 'ace/lib/oop', 'ace/mode/ini',
      'ace/mode/mod_viewer_ini_highlight_rules'],
    (require, exports) => {
      const oop = require('ace/lib/oop');
      const IniMode = require('ace/mode/ini').Mode;
      const IniHighlightRules = require(
        'ace/mode/mod_viewer_ini_highlight_rules').IniHighlightRules;
      const Mode = function () {
        IniMode.call(this);
        this.HighlightRules = IniHighlightRules;
        this.$id = 'ace/mode/mod_viewer_ini';
      };
      oop.inherits(Mode, IniMode);
      exports.Mode = Mode;
    });
}