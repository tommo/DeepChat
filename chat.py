import sublime
import sublime_plugin
import urllib.parse
import urllib.request
import json
import threading
import socket
import re
import time




class DeepChatSelectModelCommand(sublime_plugin.WindowCommand):
    def run(self, model_name):
        if model_name != "loading":
            self.window.active_view().run_command("deep_seek_chat", {"command":"set_model", "model_name": model_name})

class DeepSeekChatCommand(sublime_plugin.WindowCommand):
    def __init__(self, view):
        super().__init__(view)
        self.reset_history()
        self.active_model = None  # Store the currently active model
        self.stopping = False
        self.load_last_model() # Load last used model

    def reset_history(self):
        self.history = [
            {'role': 'system', 'content': self.get_system_message()}
        ]
        self.added_files = {}
        self.adding_file = None
        self.result_view = None

    def run(self):
        self.show_input_panel()

    def show_input_panel(self):
        self.window.show_input_panel("Deep Chat:", "", self.on_done, None, None)

    def find_output_view(self):
        self.result_view = None
        for view in self.window.views():
            if view.name() == "DeepChatResult":
                self.result_view = view
                break

    def on_done(self, message):
        self.find_output_view()
        clean_message = message.strip().lower()

        if clean_message == '': return
        if clean_message == '/stop':
            self.stopping = True
            self.show_input_panel()
            return
        if clean_message == '/clear':
            if self.result_view:
                self.result_view.run_command('select_all')
                self.result_view.run_command('right_delete')
            self.reset_history()
            self.show_input_panel()
            self.show_current_model()
            return

        if clean_message == '/history':
            history_text = "\n--------------------\n==== [Current Chat History]:\n"
            for msg in self.history:
                if msg['role'] == 'system': continue
                prefix = "You: " if msg['role'] == 'user' else "Assistant: "
                history_text += prefix + msg['content'] + "\n\n[End Of History]\n"
            self.result_view.run_command('append', {'characters': history_text})
            self.show_input_panel()
            return

        # Model command
        if clean_message == '/list':
            self.show_model_list()
            self.show_input_panel()
            return

        if clean_message.startswith('/model'):
            parts = message.split(':')
            if len(parts) > 1:
                model_name = parts[1].strip()
                self.set_active_model(model_name)  # Set the model
            else:
                self.result_view.run_command('append', {'characters': "\n[Error]: Invalid /model command format. Use /model:model_name\n"})
            self.show_input_panel()
            return


        if clean_message.startswith('/file'):
            active_view = self.window.active_view()
            if active_view:
                file_content = active_view.substr(sublime.Region(0, active_view.size()))
                file_name = active_view.file_name() or "untitled"
                self.history.append({'role': 'system', 'content': "Here is the content of {}:\n{}".format(file_name, file_content)})
                self.adding_file = file_name

        self.history.append({'role': 'user', 'content': message})
        self.user_message = message
        self.open_output_view()
        self.send_message()
        self.show_input_panel()

    def open_output_view(self):
        self.find_output_view()

        if not self.result_view:
            self.result_view = self.window.new_file()
            self.result_view.set_name("DeepChatResult")
            self.result_view.set_scratch(True)
            self.result_view.set_read_only(False)
            self.result_view.assign_syntax("Packages/DeepChat/ChatResult.tmLanguage")
            self.result_view.settings().set("word_wrap", True)
            self.show_current_model()  # Show the model on creation

        self.window.focus_view(self.result_view)
        if self.window.active_group() != self.window.get_view_index(self.result_view)[0]:
            self.window.set_view_index(self.result_view, self.window.active_group(), 0)
        self.window.run_command("focus_neighboring_group") # focus on current group
        self.window.focus_view(self.result_view) # focus again
        sublime.active_window().run_command("move_to_front")
        
        if self.adding_file:
            self.result_view.run_command('append', {'characters': "\n[Attached file: {}]".format(self.adding_file)})
            self.adding_file = False

    def set_active_model_from_command(self, model_name):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        available_models = settings.get('models', {})

        if model_name in available_models:
            self.active_model = model_name
            self.save_last_model(model_name)  # Save to view settings
            if not self.result_view:
                self.find_output_view()
            if self.result_view:
                self.result_view.run_command('append', {'characters': "\n[Model set to: {}]\n".format(model_name)})
            else:
                print("No result view")
        else:
            if self.result_view:
                self.result_view.run_command('append', {'characters': "\n[Error]: Model '{}' not found in settings.\n".format(model_name)})
            print("Model not exists")
        sublime.set_timeout_async(self.update_commands, 0)

    def set_active_model(self, model_name):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        available_models = settings.get('models', {})

        if model_name in available_models:
            self.active_model = model_name
            self.save_last_model(model_name) # Save to view settings
            self.result_view.run_command('append', {'characters': "\n[Model set to: {}]\n".format(model_name)})
        else:
            self.result_view.run_command('append', {'characters': "\n[Error]: Model '{}' not found in settings.\n".format(model_name)})

    def show_model_list(self):
        self.open_output_view()
        settings = sublime.load_settings('DeepChat.sublime-settings')
        available_models = settings.get('models', {})
        model_list_text = "\n==== [Available Models]:\n"
        for model_name, model_config in available_models.items():
            model_list_text += "- {}:   {}\n".format(model_name, model_config.get('description', '...'))
        model_list_text += "\n"
        self.result_view.run_command('append', {'characters': model_list_text})

    def show_current_model(self):
        # Show model info in result view
        if not self.result_view: return

        if self.active_model:
            self.result_view.run_command('append', {'characters': "\n[Current Model: {}]\n".format(self.active_model)})
        else:
            self.result_view.run_command('append', {'characters': "\n[Using default model. /list to show models]\n"})

    def send_message(self):
        self.stopping = False
        settings = sublime.load_settings('DeepChat.sublime-settings')

        # Use active model, or default from settings
        model_to_use = self.active_model or settings.get('default_model', 'deepseek-chat')
        available_models = settings.get('models', {})
        model_config = available_models.get(model_to_use)

        if not model_config:
             sublime.error_message("Configuration for model '{}' not found.".format(model_to_use))
             return

        api_key = model_config.get('api_key', None)
        url = model_config.get("url", None)  # Use URL from config

        if not api_key:
            sublime.error_message("API key not set. Please add your API key to DeepChat.sublime-settings.")
            return

        if not url:
            sublime.error_message("API URL not set")
            return

        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + api_key
        }

        data_dict = {
            "model": model_config.get("name", model_to_use),  # Fallback to model_to_use
            "messages": self.history,
            "max_tokens": model_config.get('max_tokens', 100),
            "temperature": model_config.get('temperature', 0.7),
            "stream": model_config.get('stream', False),
        }

        data_dict.update(model_config.get("extra", {}))

        if  model_config.get("name", model_to_use) == "deepseek-reasoner":
            del data_dict["temperature"]

        data_json = json.dumps(data_dict)
        data_bytes = data_json.encode('utf-8')

        request = urllib.request.Request(url, data_bytes, headers)

        print(url, headers)

        stream = model_config.get('stream', False)
        formatted_message = "\n--------\nQ:  {}\n\n".format(self.user_message)
        self.result_view.run_command('append', {'characters': formatted_message})
        if stream:
            self.response_buffer = b''
            self.parse_buffer = b''
            self.reply = ''
            self.response_complete = False
            self.timer_running = False
            self.previous_reply_length = 0
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

        import time
    import re  # Add this import

    def stream_response(self, request):
        """Handle streaming responses from the LLM with improved buffer handling"""
        self.reply = ''
        self.previous_reply_length = 0
        self.last_update_time = time.time()
        self.response_watchdog_active = True
        self.partial_json = ""  # For handling split JSON
        
        # Start watchdog timer in a separate thread
        watchdog_thread = threading.Thread(target=self._stream_watchdog)
        watchdog_thread.daemon = True
        watchdog_thread.start()
        
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                self.parse_buffer = b''
                
                while True and not self.stopping:
                    try:
                        # Set a short read timeout
                        self._safely_set_timeout(response, 5)
                        chunk = response.read(1024)  # Smaller chunks for more frequent updates
                        self.last_update_time = time.time()
                        
                        if not chunk:
                            print("End of stream reached")
                            # Process any remaining data in buffer
                            self._process_buffer(final=True)
                            break
                        
                        # Append to buffer and process
                        self.parse_buffer += chunk
                        self._process_buffer()
                        
                        # Start UI update timer if not already running
                        if not self.timer_running:
                            sublime.set_timeout(self.update_view, 100)
                            self.timer_running = True
                        
                    except (socket.timeout, socket.error) as e:
                        print("Socket timeout or error:", str(e))
                        # Try again (watchdog will handle true hangs)
                        continue
                        
                    except Exception as e:
                        print("Stream error:", str(e))
                        break
        
        except Exception as e:
            print("Connection error:", str(e))
            if not self.reply:
                self.reply = "Error connecting to model: " + str(e)
        
        finally:
            # Clean up and ensure final update
            self.response_watchdog_active = False
            self._process_partial_json()  # Process any remaining partial JSON
            
            if self.reply:
                self.response_complete = True
                self.history.append({'role': 'assistant', 'content': self.reply})
                sublime.set_timeout(lambda: self.update_view(final=True), 0)

    def _process_buffer(self, final=False):
        """Process the current buffer with improved JSON handling"""
        # Split on newlines but maintain buffer integrity
        if b'\n' in self.parse_buffer:
            lines = self.parse_buffer.split(b'\n')
            self.parse_buffer = lines.pop()  # Keep incomplete line in buffer
            
            for line in lines:
                self._process_line(line)
        elif final:
            # Process any remaining data if this is the final call
            self._process_line(self.parse_buffer)
            self.parse_buffer = b''

    def _process_line(self, line):
        """Process a single line with improved JSON parsing"""
        if not line.strip():
            return
        
        try:
            line_str = line.decode('utf-8', errors='replace').strip()
            
            # Handle SSE format (data: {...})
            if line_str.startswith('data: '):
                if line_str == "data: [DONE]":
                    return
                
                json_str = line_str[6:]  # Remove 'data: ' prefix
                self._handle_json_content(json_str)
            
            # Handle raw JSON lines
            elif line_str.startswith('{'):
                self._handle_json_content(line_str)
                
        except Exception as e:
            print("Error processing line:", str(e), "Line:", line)

    def _handle_json_content(self, json_str):
        """Handle JSON content with support for partial JSON"""
        try:
            # Try to parse as complete JSON
            data = json.loads(json_str)
            self._extract_content(data)
            
        except ValueError:
            # This might be a partial JSON object
            self.partial_json += json_str
            
            # Try to extract complete JSON objects from the accumulated partial JSON
            self._process_partial_json()

    def _process_partial_json(self):
        """Process accumulated partial JSON content"""
        if not self.partial_json:
            return
            
        # Look for complete JSON objects using regex
        pattern = r'(\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
        matches = re.findall(pattern, self.partial_json)
        
        for match in matches:
            try:
                data = json.loads(match)
                self._extract_content(data)
                # Remove this object from partial_json
                self.partial_json = self.partial_json.replace(match, '', 1)
            except ValueError:
                pass  # Not valid JSON

    def _extract_content(self, data):
        """Extract content with expanded format handling"""
        # Check for OpenAI/Anthropic style format
        if 'choices' in data:
            choices = data.get('choices', [])
            if choices and len(choices) > 0:
                choice = choices[0]
                
                # OpenAI streaming format
                if 'delta' in choice:
                    delta = choice.get('delta', {})
                    if 'content' in delta:
                        content = delta.get('content')
                        if content is not None:
                            self.reply += content
                
                # Regular format
                elif 'message' in choice:
                    message = choice.get('message', {})
                    if 'content' in message:
                        content = message.get('content')
                        if content is not None:
                            self.reply += content
                
                # Text completion format
                elif 'text' in choice:
                    text = choice.get('text')
                    if text is not None:
                        self.reply += text
        
        # Sonnet and other non-standard formats
        elif 'text' in data:
            text = data.get('text')
            if text is not None:
                self.reply += text
        
        elif 'content' in data:
            content = data.get('content')
            if content is not None:
                self.reply += content
                
        # Claude/Anthropic format
        elif 'completion' in data:
            completion = data.get('completion')
            if completion is not None:
                self.reply += completion
                
        # Mistral/Mixtral format
        elif 'response' in data:
            response = data.get('response')
            if response is not None:
                self.reply += response

    def _stream_watchdog(self):
        """Watchdog timer to detect and recover from stream hangs"""
        while self.response_watchdog_active:
            time.sleep(1)  # Check every second
            
            current_time = time.time()
            elapsed = current_time - self.last_update_time
            
            # If more than 15 seconds without updates, consider it hanging
            if elapsed > 15 and not self.response_complete:
                print("Watchdog detected potential hang after", elapsed, "seconds")
                self.response_watchdog_active = False
                
                # Force completion of the response
                if self.reply:
                    sublime.set_timeout(lambda: self._handle_hang(), 0)
                return

    def _handle_hang(self):
        """Handle a detected stream hang"""
        if not self.response_complete:
            self.reply += "\n\n[Response incomplete - stream timed out]"
            self.response_complete = True
            self.stopping = True
            self.history.append({'role': 'assistant', 'content': self.reply})
            self.update_view(final=True)

    def _safely_set_timeout(self, response, timeout=10):
        """Safely set timeout on socket if available"""
        try:
            if hasattr(response, 'fp') and response.fp is not None:
                if hasattr(response.fp, 'raw') and response.fp.raw is not None:
                    if hasattr(response.fp.raw, '_sock') and response.fp.raw._sock is not None:
                        response.fp.raw._sock.settimeout(timeout)
        except Exception as e:
            print("Could not set socket timeout:", str(e))


    def update_view(self, final=False):
        if not self.timer_running and not final: return
        if not self.result_view or not self.result_view.is_valid():
            self.response_complete = True
            return
        if final:
            new_content = self.reply[self.previous_reply_length:]
            self.result_view.run_command('append', {'characters': new_content})
            self.result_view.run_command('append', {'characters': '\n'})
            self.previous_reply_length = len(self.reply)
        else:
            new_content = self.reply[self.previous_reply_length:]
            self.result_view.run_command('append', {'characters': new_content})
            self.previous_reply_length = len(self.reply)
        cursor_pos = self.result_view.sel()[0].b
        if cursor_pos == self.result_view.size():
            self.result_view.sel().clear()
            self.result_view.sel().add(sublime.Region(self.result_view.size()))
        if not final:
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
            result_view.assign_syntax("Packages/DeepChat/ChatResult.tmLanguage")
            result_view.set_read_only(False)

        formatted_message = "{}\n\n".format(reply)
        result_view.run_command('append', {'characters': formatted_message})
        result_view.sel().clear()
        result_view.sel().add(sublime.Region(result_view.size()))
        self.window.focus_view(result_view)

    def get_system_message(self):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        return settings.get('system_message', 'You are a helpful assistant.')

    def load_last_model(self):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        self.active_model = settings.get('last_active_model', None)

    def save_last_model(self, model_name):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        settings.set('last_active_model', model_name)
        sublime.save_settings('DeepChat.sublime-settings')


