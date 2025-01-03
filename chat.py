import sublime
import sublime_plugin
import urllib.parse
import urllib.request
import json
import threading

class DeepSeekChatCommand(sublime_plugin.WindowCommand):

    def run(self):
        self.history = [
            {'role': 'system', 'content': self.get_system_message()}
        ]
        self.show_input_panel()

    def show_input_panel(self):
        self.window.show_input_panel("Deep Chat:", "", self.on_done, None, None)

    def on_done(self, message):
        if message.strip().lower() == '':
            return

        self.history.append({'role': 'user', 'content': message})
        self.user_message = message  # Store the user's message
        self.open_output_view()
        self.send_message()

        # Show the input panel again for the next message
        self.show_input_panel()

    def open_output_view(self):
        # Open or reuse the output view
        self.result_view = None
        for view in self.window.views():
            if view.name() == "DeepChatResult":
                self.result_view = view
                break
        if not self.result_view:
            self.result_view = self.window.new_file()
            self.result_view.set_name("DeepChatResult")
            self.result_view.set_scratch(True)
            self.result_view.set_syntax_file("Packages/Text/Plain text.tmLanguage")
            self.result_view.set_read_only(False)
            self.result_view.settings().set("word_wrap", True)
        # Clear the view content
        self.result_view.run_command('select_all')
        self.result_view.run_command('erase')
        # Show the view
        self.window.focus_view(self.result_view)

    def send_message(self):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        api_key = settings.get('api_key')
        model = settings.get('model', 'deepseek-chat')

        if not api_key:
            sublime.error_message("API key not set. Please add your API key to DeepChat.sublime-settings.")
            return

        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + api_key
        }

        data_dict = {
            "model": model,
            "messages": self.history,
            "max_tokens": settings.get('max_tokens', 100),
            "temperature": settings.get('temperature', 0.7),
            "stream": settings.get('stream', False),
        }

        data_json = json.dumps(data_dict)
        data_bytes = data_json.encode('utf-8')

        url = 'https://api.deepseek.com/beta/chat/completions'
        request = urllib.request.Request(url, data_bytes, headers)

        stream = settings.get('stream', False)
        formatted_message = "\n--------\nQ:  {}\n\n".format(self.user_message)
        self.result_view.run_command('append', {'characters': formatted_message})
        if stream:
            self.response_buffer = b''
            self.parse_buffer = b''
            self.reply = ''
            self.response_complete = False
            self.timer_running = False
            self.previous_reply_length = 0

            # Start a thread to handle streaming response
            threading.Thread(target=self.stream_response, args=(request,)).start()
        else:
            # Non-streaming mode remains the same
            try:
                with urllib.request.urlopen(request) as response:
                    response_bytes = response.read()
                    response_str = response_bytes.decode('utf-8')
                    response_json = json.loads(response_str)
                    choices = response_json.get('choices', [])
                    if choices:
                        reply = choices[0].get('message', {}).get('content', 'No reply from the API.')
                        self.history.append({'role': 'assistant', 'content': reply})
                    else:
                        reply = 'No reply from the API.'

                    # Display the response in the result view along with the user's message
                    self.display_response(self.user_message, reply)

            except urllib.error.HTTPError as e:
                sublime.error_message("HTTP Error: %d - %s" % (e.code, e.reason))
                print("HTTP Error: %d - %s" % (e.code, e.reason))
            except urllib.error.URLError as e:
                sublime.error_message("URL Error: %s" % e.reason)
                print("URL Error: %s" % e.reason)
            except Exception as e:
                sublime.error_message("An error occurred: " + str(e))
                print("An error occurred: " + str(e))

    def stream_response(self, request):
        self.reply = ''
        self.previous_reply_length = 0
        try:
            with urllib.request.urlopen(request) as response:
                while True:
                    chunk = response.read(1024)
                    if not chunk:
                        break
                    self.parse_buffer += chunk
                    # Split the buffer by newline and parse each JSON object
                    lines = self.parse_buffer.split(b'\n')
                    self.parse_buffer = lines[-1]  # Keep the last line which may be incomplete
                    for line in lines[:-1]:
                        if line:
                            try:
                                obj_str = line.decode('utf-8')
                                data = json.loads(obj_str[5:])
                                choices = data.get('choices', [])
                                if choices:
                                    delta = choices[0].get('delta', {})
                                    content = delta.get('content', '')
                                    self.reply += content
                                else:
                                    self.reply = 'No reply from the API.'
                            except ValueError as e:
                                print("INVALID JSON", e)
                                # Invalid JSON, skip this line
                                continue
                    # Schedule the view update
                    if not self.timer_running:
                        sublime.set_timeout(self.update_view, 100)
                        self.timer_running = True
                # Mark response as complete
                self.response_complete = True
                # Final update
                sublime.set_timeout(lambda: self.update_view(final=True), 0)
        except urllib.error.HTTPError as e:
            sublime.error_message("HTTP Error: %d - %s" % (e.code, e.reason))
            print("HTTP Error: %d - %s" % (e.code, e.reason))
        except urllib.error.URLError as e:
            sublime.error_message("URL Error: %s" % e.reason)
            print("URL Error: %s" % e.reason)
        except Exception as e:
            sublime.error_message("An error occurred: " + str(e))
            print("An error occurred: " + str(e))

    def update_view(self, final=False):
        if not self.timer_running and not final:
            return
        # Check if the view is still valid
        if not self.result_view or not self.result_view.is_valid():
            self.response_complete = True
            return
        # Calculate the new content to append
        if final:
            new_content = self.reply[self.previous_reply_length:]
            # Append the new content to the view
            self.result_view.run_command('append', {'characters': new_content})
            # Optionally, add a newline or marker to indicate the end of the reply
            self.result_view.run_command('append', {'characters': '\n'})
            # Update the previous reply length
            self.previous_reply_length = len(self.reply)
        else:
            # Append the new content to the view
            new_content = self.reply[self.previous_reply_length:]
            self.result_view.run_command('append', {'characters': new_content})
            # Update the previous reply length
            self.previous_reply_length = len(self.reply)
        # Set the cursor to the end
        self.result_view.sel().clear()
        self.result_view.sel().add(sublime.Region(self.result_view.size()))
        if not final:
            # Schedule the next update
            sublime.set_timeout(self.update_view, 100)
        else:
            self.timer_running = False

    def display_response(self, user_message, reply):
        result_view = None
        for view in self.window.views():
            if view.name() == "DeepChatResult":
                result_view = view
                break

        if not result_view:
            result_view = self.window.new_file()
            result_view.set_name("DeepChatResult")
            result_view.set_scratch(True)
            result_view.set_syntax_file("Packages/Text/Plain text.tmLanguage")
            result_view.set_read_only(False)

        # Format the messages with prefixes and empty lines
        formatted_message = "{}\n\n".format(user_message, reply)
        # Append the formatted messages to the result view
        result_view.run_command('append', {'characters': formatted_message})
        # Set the cursor to the end of the view
        result_view.sel().clear()
        result_view.sel().add(sublime.Region(result_view.size()))
        # Show and focus on the view
        self.window.focus_view(result_view)

    def get_system_message(self):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        return settings.get('system_message', 'You are a helpful assistant.')