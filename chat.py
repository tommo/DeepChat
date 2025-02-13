import sublime
import sublime_plugin
import urllib.parse
import urllib.request
import json
import threading

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

    def stream_response(self, request):
        self.reply = ''
        self.previous_reply_length = 0
        try:
            with urllib.request.urlopen(request) as response:
                while True:
                    if self.stopping:
                        response.close()
                        break
                    chunk = response.read(4096)
                    if not chunk: break
                    self.parse_buffer += chunk
                    lines = self.parse_buffer.split(b'\n')
                    self.parse_buffer = lines[-1]
                    for line in lines[:-1]:
                        if line:
                            try:
                                obj_str = line.decode('utf-8')
                                if obj_str != "data: [DONE]":
                                    data = json.loads(obj_str[5:])
                                    choices = data.get('choices', [])
                                    if choices:
                                        delta = choices[0].get('delta', {})
                                        content = delta.get('content', '')
                                        if content != None:
                                            self.reply += content
                                    else:
                                        self.reply = 'No reply from the API.'
                            except ValueError as e:
                                print(line)
                                print("INVALID JSON", e)
                                continue
                    if not self.timer_running:
                        sublime.set_timeout(self.update_view, 100)
                        self.timer_running = True
                self.response_complete = True
                self.history.append({'role': 'assistant', 'content': self.reply})
                sublime.set_timeout(lambda: self.update_view(final=True), 0)
        except urllib.error.HTTPError as e:
            sublime.error_message("HTTP Error: %d - %s" % (e.code, e.reason))
            print("HTTP Error: %d - %s" % (e.code, e.reason))
        except urllib.error.URLError as e:
            sublime.error_message("URL Error: %s" % e.reason)
            print("URL Error: %s" % e.reason)
        except Exception as e:
            sublime.error_message("An error occurred: " + str(e))
            raise e

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


