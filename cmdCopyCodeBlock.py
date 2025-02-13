import sublime
import sublime_plugin
import re

class CopyMarkdownCodeBlockCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        # Get the current cursor position
        cursor_position = self.view.sel()[0].begin()

        # Get the entire document content
        content = self.view.substr(sublime.Region(0, self.view.size()))

        # Find the code block at the cursor position
        code_block_pattern = re.compile(r'```.*?\n(.*?)```', re.DOTALL)
        for match in code_block_pattern.finditer(content):
            start, end = match.span(1)
            if start <= cursor_position <= end:
                code_block = match.group(1).strip()
                code_block_region = sublime.Region(start, end)
                self.view.sel().clear()
                self.view.sel().add(code_block_region)
                
                sublime.set_clipboard(code_block)
                sublime.status_message("Code block copied to clipboard")
                return

        sublime.status_message("No code block found at cursor position")