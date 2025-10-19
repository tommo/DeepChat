import sublime
import sublime_plugin
import urllib.parse
import urllib.request
import json
import threading
import socket
import re
import time
import os
import hashlib

from datetime import datetime
from .script_runner import ScriptRunner

#----------------------------------------------------------------
class DeepChatRefreshCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.run_command("deep_seek_chat", {"command": "refresh"})


#----------------------------------------------------------------
class DeepChatInsertFileCommand(sublime_plugin.WindowCommand):
    def run(self):
        active_view = self.window.active_view()
        if active_view and active_view.file_name():
            self.window.run_command("deep_seek_chat", {"add_file": active_view.file_name()})
        else:
            sublime.status_message("No active file")

#----------------------------------------------------------------
class DeepChatSelectModelCommand(sublime_plugin.WindowCommand):
    def run(self):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        available_models = settings.get('models', {})
        
        if not available_models:
            sublime.status_message("No models defined in settings")
            return
            
        self.models = []
        self.model_names = []
        
        for model_name, model_config in available_models.items():
            self.model_names.append(model_name)
            description = model_config.get('description', '...')
            self.models.append([model_name, description])
            
        self.window.show_quick_panel(
            self.models, 
            self.on_selected,
            sublime.MONOSPACE_FONT
        )
    
    def on_selected(self, index):
        if index == -1:  # User cancelled
            return
            
        selected_model = self.model_names[index]
        self.window.run_command("deep_seek_chat", {"command": "set_model", "model_name": selected_model})


#----------------------------------------------------------------
class SessionManager:
    """Manage chat sessions"""
    
    @staticmethod
    def get_sessions_dir(window=None):
        """Get or create sessions directory - prefer project root"""
        # Try project root first
        if window:
            folders = window.folders()
            if folders:
                project_dir = os.path.join(folders[0], '.deepchat', 'sessions')
                os.makedirs(project_dir, exist_ok=True)
                return project_dir
        
        # Fallback to user directory
        user_dir = sublime.packages_path()
        sessions_dir = os.path.join(user_dir, 'User', 'DeepChat', 'sessions')
        os.makedirs(sessions_dir, exist_ok=True)
        return sessions_dir
    
    @staticmethod
    def generate_session_id(name=None):
        """Generate unique session ID"""
        if name:
            return re.sub(r'[^\w\-]', '_', name.lower())
        else:
            return datetime.now().strftime('%Y%m%d_%H%M%S')
    
    @staticmethod
    def save_session(session_id, data, window=None):
        """Save session to file"""
        sessions_dir = SessionManager.get_sessions_dir(window)
        file_path = os.path.join(sessions_dir, '{}.session.json'.format(session_id))
        
        session_data = {
            'id': session_id,
            'created_at': data.get('created_at', datetime.now().isoformat()),
            'updated_at': datetime.now().isoformat(),
            'active_model': data.get('active_model'),
            'history': data.get('history', []),
            'added_files': data.get('added_files', {}),
            'metadata': data.get('metadata', {})
        }
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, indent=2, ensure_ascii=False)
        
        return file_path
    
    @staticmethod
    def load_session(session_id, window=None):
        """Load session from file"""
        sessions_dir = SessionManager.get_sessions_dir(window)
        file_path = os.path.join(sessions_dir, '{}.session.json'.format(session_id))
        if not os.path.exists(file_path):
            return None
        
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    @staticmethod
    def list_sessions(window=None):
        """List all available sessions"""
        sessions_dir = SessionManager.get_sessions_dir(window)
        sessions = []
        
        for filename in os.listdir(sessions_dir):
            if filename.endswith('.session.json'):
                file_path = os.path.join(sessions_dir, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        sessions.append({
                            'id': data.get('id'),
                            'created_at': data.get('created_at'),
                            'updated_at': data.get('updated_at'),
                            'model': data.get('active_model'),
                            'message_count': len(data.get('history', [])),
                            'file_path': file_path
                        })
                except:
                    continue
        
        sessions.sort(key=lambda x: x.get('updated_at', ''), reverse=True)
        return sessions
    
    @staticmethod
    def delete_session(session_id, window=None):
        """Delete a session"""
        sessions_dir = SessionManager.get_sessions_dir(window)
        file_path = os.path.join(sessions_dir, '{}.session.json'.format(session_id))
        
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        return False

#----------------------------------------------------------------
class DeepSeekChatCommand(sublime_plugin.WindowCommand):
    def __init__(self, view):
        super().__init__(view)
        self.available_functions = {}
        self.active_model = None
        self.stopping = False
        self.content_lock = threading.Lock()
        self.load_last_model()
        self.current_session_id = None
        self.auto_save = True 
        self.auto_resume_attempted = False
        self.script_runner = ScriptRunner(self)
        self.in_script_mode = False
        self.history = []
        self.message_id = 0
        self.labels = {}  # label_name -> message_id
        self.discover_functions()
        self.reset_history()


    def reset_history(self):
        self.history = [
            {'role': 'system', 'content': self.get_system_message()},
        ]
        self.added_files = {}
        self.adding_file = None
        self.result_view = None
        self.history_view = None
        self.current_session_id = None
        self.message_id = 0
        self.labels = {}
        self.try_load_knowledge_base()

    def add_message_to_history(self, role, content, label=None):
        """Add message to history with auto-incrementing ID"""
        self.message_id += 1
        msg = {
            'role': role,
            'content': content,
            'id': self.message_id
        }
        if label:
            self.labels[label] = self.message_id
            msg['label'] = label
        self.history.append(msg)
        return self.message_id

    def run(self, **options):
        if options.get('command') == 'set_model':
            self.set_active_model_from_command(options.get('model_name', ''))
            return

        if options.get('command') == 'run_script':
            script_path = options.get('script_path')
            if script_path and self.script_runner.load_script(script_path):
                self.script_runner.execute_script()
            return
        
        if options.get('command') == 'continue_script':
            if self.script_runner.current_script:
                self.script_runner.execute_next_step()
            else:
                self.append_message("\n[No active script]\n")
            return
        if options.get('command') == 'refresh':
            self.discover_functions()
            self.append_message("\n[Refreshed functions: {} found]\n".format(
                len(self.available_functions)
            ))
            if self.available_functions:
                for cmd_name in self.available_functions.keys():
                    self.append_message("  - {}\n".format(cmd_name))
            
            # Reload knowledge base
            kb_loaded = self.reload_knowledge_base()
            if kb_loaded:
                self.append_message("[Knowledge base reloaded]\n")
            
            self.show_input_panel()
            return
        
        if not self.auto_resume_attempted:
            self.auto_resume_attempted = True
            if self.try_auto_resume():
                return

        if options.get('add_file'):
            self.add_file(options.get('add_file'))
        
        self.show_input_panel()

    # File handling methods
    def add_file(self, file_path, content=None):
        """Add file reference to history (content loaded on-demand via view_file)"""
        try:
            # Just verify file exists
            if content is None and not os.path.exists(file_path):
                self.append_message("\n[Error: File not found: {}]\n".format(file_path))
                return
            
                        # Get file size for reference
            if content is None:
                file_size = os.path.getsize(file_path)
            else:
                file_size = len(content.encode('utf-8'))
            
            # Add lightweight reference instead of full content
            line = {
                'role': 'system', 
                'content': "File available: {} (use view_file to read content)".format(file_path)
            }
            
            if file_path in self.added_files:
                self.append_message("\n[File already attached: {}]\n".format(file_path))
            else:
                self.history.append(line)
                self.adding_file = file_path
                self.added_files[file_path] = line
                self.append_message("\n[Attached file: {} ({} bytes)]\n".format(file_path, file_size))

        except Exception as e:
            self.append_message("\n[Error attaching file: {}]\n".format(str(e)))

    def show_file_list(self):
        self.open_output_view()
        if not self.added_files:
            self.result_view.run_command('append', {'characters': "\n[No files attached in current session]\n"})
        else:
            files_text = "\n==== [Attached Files]:\n"
            for file_path in self.added_files:
                files_text += "- {}\n".format(file_path)
            self.result_view.run_command('append', {'characters': files_text})

    # UI methods
    def show_input_panel(self):
        self.window.show_input_panel("Deep Chat:", "", self.on_done, None, None)

    def append_message(self, message, grab_focus=False):
        if not self.result_view or not self.result_view.is_valid():
            if not grab_focus:
                # Don't create view for hint messages
                return
            self.open_output_view(grab_focus=grab_focus)
        self.result_view.run_command('append', {'characters': message})

    def open_output_view(self, grab_focus=True):
        self.find_output_view()

        if not self.result_view:
            self.result_view = self.window.new_file()
            self.result_view.set_name("DeepChatResult")
            self.result_view.set_scratch(True)
            self.result_view.set_read_only(False)
            self.result_view.assign_syntax("Packages/DeepChat/ChatResult.tmLanguage")
            self.result_view.settings().set("word_wrap", True)
            self.show_current_model()

        if grab_focus:
            self.window.focus_view(self.result_view)
            if self.window.active_group() != self.window.get_view_index(self.result_view)[0]:
                self.window.set_view_index(self.result_view, self.window.active_group(), 0)
            self.window.run_command("focus_neighboring_group")
            self.window.focus_view(self.result_view)
            sublime.active_window().run_command("move_to_front")

    def find_output_view(self):
        self.result_view = None
        for view in self.window.views():
            if view.name() == "DeepChatResult":
                self.result_view = view
                break
    def display_history(self):
        """Display conversation history in separate view"""
        self.open_history_view()
        
        # Clear existing content
        self.history_view.run_command('select_all')
        self.history_view.run_command('right_delete')
        
        # Header
        header = "==== Conversation History ====\n"
        header += "Session: {}\n".format(self.current_session_id or "unsaved")
        header += "Model: {}\n".format(self.active_model or "none")
        header += "Messages: {}\n\n".format(len(self.history))
        self.history_view.run_command('append', {'characters': header})
        
        # Display each message
        for i, msg in enumerate(self.history):
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
            msg_id = msg.get('id', i)
            label = msg.get('label', '')
            
            # Format message header
            if role == 'system':
                prefix = "# system:"
            elif role == 'user':
                prefix = "\n----\n# [User] [{}]:".format(msg_id)
                if label:
                    prefix += " <{}>".format(label)
            else:
                prefix = "# [Answer]: "
            
            # Truncate long content for preview
            preview = content[:200]
            if len(content) > 200:
                preview += "..."
            
            msg_text = "{}\n{}\n\n".format(prefix, preview)
            self.history_view.run_command('append', {'characters': msg_text})
        
        # Footer with instructions
        footer = "\n==== Commands ====\n"
        footer += "Use /rewind:<id|label> to rewind to a message\n"
        self.history_view.run_command('append', {'characters': footer})
        
        self.show_input_panel()
    def open_history_view(self):
        """Open/focus the history view"""
        # Find existing history view
        history_view = None
        for view in self.window.views():
            if view.name() == "DeepChatHistory":
                history_view = view
                break
        
        if not history_view:
            history_view = self.window.new_file()
            history_view.set_name("DeepChatHistory")
            history_view.set_scratch(True)
            history_view.set_read_only(False)
            history_view.assign_syntax("Packages/Markdown/Markdown.sublime-syntax")
            history_view.settings().set("word_wrap", True)
        
        self.history_view = history_view
        self.window.focus_view(history_view)
        return history_view

    # Command handling
    def on_done(self, message):
        self.find_output_view()
        clean_message = message.strip().lower()

        if not clean_message:
            return
            
        # Command handling
        if clean_message == '/save':
            self.handle_save_command(message)
            return
        
        if clean_message == '/load':
            self.show_session_list('load')
            return
        
        if clean_message == '/sessions':
            self.show_session_list('info')
            return

        if clean_message.startswith('/new'):
            if len(self.history) > 1 and self.current_session_id:
                self.auto_save_session()
            
            # Clear and start fresh
            if self.result_view:
                self.result_view.run_command('select_all')
                self.result_view.run_command('right_delete')
            
            self.reset_history()
            # Refresh system message with functions
            if self.history and self.history[0]['role'] == 'system':
                self.history[0]['content'] = self.get_system_message()
            
            # Handle optional session name
            parts = message.split(':', 1)
            if len(parts) > 1:
                session_name = parts[1].strip()
                self.current_session_id = SessionManager.generate_session_id(session_name)
            else:
                self.current_session_id = None
            
            self.show_current_model()
            session_info = " ({})".format(self.current_session_id) if self.current_session_id else ""
            self.append_message("\n[New session started{}]\n".format(session_info))
            self.show_input_panel()
            return
        
        if clean_message.startswith('/save:'):
            session_name = message[6:].strip()
            self.save_current_session(session_name)
            self.show_input_panel()
            return
        
        if clean_message.startswith('/load:'):
            session_id = message[6:].strip()
            self.load_session(session_id)
            self.show_input_panel()
            return
        
        if clean_message.startswith('/delete:'):
            session_id = message[8:].strip()
            self.delete_session(session_id)
            self.show_input_panel()
            return

        if clean_message == '/stop':
            self.stopping = True
            self.show_input_panel()
            return
            
        if clean_message.startswith('/testcall'):
            result = self.process_response_with_functions(clean_message[9:].strip())
            self.append_message("\n[Test Result]: \n {}\n".format(result))
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
            self.display_history()
            return

        if clean_message.startswith('/rewind:'):
            target = message[8:].strip()
            self.rewind_to(target)
            self.show_input_panel()
            return
        
        if clean_message.startswith('/label:'):
            parts = message[7:].split(None, 1)
            if len(parts) == 2:
                label_name, user_msg = parts
                self.add_message_to_history('user', user_msg, label=label_name)
                self.user_message = user_msg
                self.open_output_view()
                self.send_message_with_retry()
            else:
                self.append_message("\n[Error: /label:name <message>]\n")
            self.show_input_panel()
            return

        if clean_message == '/list':
            self.show_model_list()
            self.show_input_panel()
            return

        if clean_message == '/list_file':
            self.show_file_list()
            self.show_input_panel()
            return
        
        if clean_message == '/system_hint':
            self.append_message("\n[System Hint]:\n{}\n".format(
                self.get_system_message()
            ))
            return

        if clean_message == '/settings':
            settings_path = os.path.join(
                sublime.packages_path(), 
                'User', 
                'DeepChat.sublime-settings'
            )
            if not os.path.exists(settings_path):
                os.makedirs(os.path.dirname(settings_path), exist_ok=True)
                default_settings = sublime.load_resource(
                    'Packages/DeepChat/DeepChat.sublime-settings'
                )
                with open(settings_path, 'w', encoding='utf-8') as f:
                    f.write(default_settings)
            self.window.open_file(settings_path)
            return
            
        if clean_message == '/source':
            source_path = os.path.join(
                sublime.packages_path(), 
                'DeepChat', 
                'chat.py'
            )
            self.window.open_file(source_path)
            return

        if clean_message == '/auto_resume':
            settings = sublime.load_settings('DeepChat.sublime-settings')
            current = settings.get('auto_resume', True)
            settings.set('auto_resume', not current)
            sublime.save_settings('DeepChat.sublime-settings')
            self.append_message("\n[Auto-resume: {}]\n".format('ON' if not current else 'OFF'))
            self.show_input_panel()
            return

        if clean_message == '/script':
            self.window.run_command('deep_chat_run_script')
            return
        
        if clean_message == '/continue':
            if self.script_runner.current_script:
                self.script_runner.execute_next_step()
            else:
                self.append_message("\n[No active script to continue]\n")
                self.show_input_panel()
            return
        if clean_message == '/refresh':
            self.discover_functions()
            self.append_message("\n[Refreshed functions: {} found]\n".format(
                len(self.available_functions)
            ))
            if self.available_functions:
                print("found tool functions:")
                for cmd_name in self.available_functions.keys():
                    print("  - {}\n".format(cmd_name))
            
            # Reload knowledge base
            kb_loaded = self.reload_knowledge_base()
            if kb_loaded:
                self.append_message("[Knowledge base reloaded]\n")
            
            self.show_input_panel()
            return
        
        if clean_message.startswith('/read'):
            active_view = self.window.active_view()
            if active_view and active_view.file_name():
                file_path = active_view.file_name()
                file_content = active_view.substr(sublime.Region(0, active_view.size()))
                self.add_file(file_path, file_content)
                self.append_message("\n[Read active file: {}]\n".format(file_path))
            else:
                self.append_message("\n[No active file to read]\n")
            self.show_input_panel()
            return

        if clean_message.startswith('/model'):
            self.handle_model_command(message)
            return

        if clean_message.startswith('/file:'):
            file_path = message[6:].strip()
            self.add_file(file_path)
            # self.show_input_panel()
            # return

        if clean_message.startswith('/file'):
            self.handle_file_command()
            # self.show_input_panel()
            # return

        # Regular message
        self.add_message_to_history('user', message)
        self.user_message = message
        self.open_output_view()
        self.send_message_with_retry()
        self.show_input_panel()

    
    def rewind_to(self, target):
        """Rewind history to specific ID or label"""
        try:
            # Try as label first
            if target in self.labels:
                target_id = self.labels[target]
            else:
                target_id = int(target)
            
            # Find message with target ID
            new_history = []
            found = False
            for msg in self.history:
                new_history.append(msg)
                if msg.get('id') == target_id:
                    found = True
                    break
            
            if found:
                self.history = new_history
                # Rebuild labels
                self.labels = {}
                for msg in self.history:
                    if 'label' in msg:
                        self.labels[msg['label']] = msg['id']
                
                self.append_message("\n[Rewound to ID {} ({} messages)]\n".format(
                    target_id, len(self.history)
                ))
                self.auto_save_session()
            else:
                self.append_message("\n[ID/label '{}' not found]\n".format(target))
        
        except ValueError:
            self.append_message("\n[Invalid ID: {}]\n".format(target))

    def handle_model_command(self, message):
        parts = message.split(':')
        if len(parts) > 1:
            model_name = parts[1].strip()
            self.set_active_model(model_name)
        else:
            self.result_view.run_command('append', 
                {'characters': "\n[Error]: Invalid /model command format. Use /model:model_name\n"})
        self.show_input_panel()

    def handle_file_command(self):
        active_view = self.window.active_view()
        if active_view:
            file_content = active_view.substr(sublime.Region(0, active_view.size()))
            file_name = active_view.file_name() or "untitled"
            self.add_file(file_name, file_content)

    # Sessions
    def handle_save_command(self, message):
        """Handle /save command"""
        parts = message.split(':', 1)
        if len(parts) > 1:
            session_name = parts[1].strip()
            self.save_current_session(session_name)
        else:
            # Save with auto-generated name
            self.save_current_session()
        self.show_input_panel()

    def try_load_knowledge_base(self):
        """Load knowledge bases from project and user directories"""
        folders = self.window.folders()
        
        kb_contents = []
        
        # Load project knowledge base
        if folders:
            project_kb = os.path.join(folders[0], '.deepchat', 'knowledge.md')
            if os.path.exists(project_kb):
                try:
                    with open(project_kb, 'r', encoding='utf-8') as f:
                        kb_contents.append(('project', f.read()))
                except Exception as e:
                    print("Failed to load project knowledge: {}".format(e))
        
        # Load user knowledge base
        user_kb = os.path.join(sublime.packages_path(), 'User', 'DeepChat', 'knowledge.md')
        if os.path.exists(user_kb):
            try:
                with open(user_kb, 'r', encoding='utf-8') as f:
                    kb_contents.append(('user', f.read()))
            except Exception as e:
                print("Failed to load user knowledge: {}".format(e))
        
        if kb_contents:
            combined = '\n\n---\n\n'.join(content for _, content in kb_contents)
            kb_message = {
                'role': 'assistant',
                'content': 'I have access to this knowledge base:\n\n' + combined
            }
            self.history.insert(1, kb_message)
            
            sources = ', '.join(source for source, _ in kb_contents)
            self.append_message("\n[Loaded knowledge: {}]\n".format(sources))
            return True
        
        return False    

    def reload_knowledge_base(self):
        """Reload knowledge base by removing old and loading fresh"""
        # Remove existing knowledge base message (at index 1 if exists)
        if len(self.history) > 1 and self.history[1]['role'] == 'assistant' and 'knowledge base' in self.history[1]['content']:
            self.history.pop(1)
        
        # Load fresh knowledge base
        return self.try_load_knowledge_base()

    def try_auto_resume(self):
        """Try to resume last session"""
        settings = sublime.load_settings('DeepChat.sublime-settings')
        
        # Check if auto-resume is enabled
        if not settings.get('auto_resume', True):
            self.try_load_knowledge_base()
            return False
        
        # Get last session ID
        last_session_id = settings.get('last_session_id')
        if not last_session_id:
            self.try_load_knowledge_base()
            return False
        
        # Try to load it
        session_data = SessionManager.load_session(last_session_id, self.window)
        if not session_data:
            self.try_load_knowledge_base()
            return False
        
        # Load the session
        self.history = session_data.get('history', [])
        self.active_model = session_data.get('active_model')
        self.current_session_id = last_session_id
        
        # Restore files
        self.added_files = {}
        for file_path, file_data in session_data.get('added_files', {}).items():
            self.added_files[file_path] = {
                'role': 'system',
                'content': file_data.get('content', '')
            }
        
        if self.history and self.history[0]['role'] == 'system':
            self.history[0]['content'] = self.get_system_message()

        # Display 
        self.open_output_view()
        self.append_message("\n# [Auto-resumed: {}]\n".format(last_session_id))
        
        # Show last few messages
        recent_messages = [m for m in self.history if m['role'] != 'system'][-4:]
        if recent_messages:
            self.append_message("# [Last messages:]\n\n")
            self.append_message("```\n")
            for msg in recent_messages:
                prefix = "- Q: " if msg['role'] == 'user' else "- A: "
                preview = msg['content'][:100] + "..." if len(msg['content']) > 100 else msg['content']
                preview = preview.replace('```', '`')
                self.append_message("{}{}\n".format(prefix, preview))
            self.append_message("```\n")
        
        self.append_message("\n")
        self.update_status_bar()
        self.show_input_panel()
        return True

    def save_current_session(self, session_name=None):
        """Save current chat session"""
        if not self.history or len(self.history) <= 1:
            self.append_message("\n[Nothing to save]\n")
            return
        
        # Generate or use existing session ID
        if session_name:
            session_id = SessionManager.generate_session_id(session_name)
        elif self.current_session_id:
            session_id = self.current_session_id
        else:
            session_id = SessionManager.generate_session_id()
        
        # Prepare session data
        session_data = {
            'active_model': self.active_model,
            'history': self.history,
            'added_files': {k: {'content': v['content']} for k, v in self.added_files.items()},
            'metadata': {
                'message_count': len([h for h in self.history if h['role'] != 'system']),
                'file_count': len(self.added_files)
            }
        }
        
        # Load existing session to preserve created_at
        existing = SessionManager.load_session(session_id)
        if existing:
            session_data['created_at'] = existing.get('created_at')
        
        # Save
        file_path = SessionManager.save_session(session_id, session_data, self.window)
        self.current_session_id = session_id
        
        # Save as last session for auto-resume
        settings = sublime.load_settings('DeepChat.sublime-settings')
        settings.set('last_session_id', session_id)
        sublime.save_settings('DeepChat.sublime-settings')
        
        self.append_message("\n[Session saved: {}]\n".format(session_id))

    def auto_save_session(self):
        """Auto-save current session"""
        if not self.current_session_id:
            # Create new session on first auto-save
            self.current_session_id = SessionManager.generate_session_id()
        
        if len(self.history) > 1:  # Has messages beyond system message
            session_data = {
                'active_model': self.active_model,
                'history': self.history,
                'added_files': {k: {'content': v['content']} for k, v in self.added_files.items()},
                'metadata': {
                    'message_count': len([h for h in self.history if h['role'] != 'system']),
                    'file_count': len(self.added_files)
                }
            }
            
            existing = SessionManager.load_session(self.current_session_id, self.window)
            if existing:
                session_data['created_at'] = existing.get('created_at')
            
            SessionManager.save_session(self.current_session_id, session_data, self.window)

            settings = sublime.load_settings('DeepChat.sublime-settings')
            settings.set('last_session_id', self.current_session_id)
            sublime.save_settings('DeepChat.sublime-settings')

    def load_session(self, session_id):
        """Load a saved session"""
        session_data = SessionManager.load_session(session_id, self.window)
        
        if not session_data:
            self.append_message("\n[Session '{}' not found]\n".format(session_id))
            return
        
        # Clear current view
        if self.result_view:
            self.result_view.run_command('select_all')
            self.result_view.run_command('right_delete')
        
        # Load session data
        self.history = session_data.get('history', [])
        self.active_model = session_data.get('active_model')
        self.current_session_id = session_id
        
        # Restore added files
        self.added_files = {}
        for file_path, file_data in session_data.get('added_files', {}).items():
            self.added_files[file_path] = {
                'role': 'system',
                'content': file_data.get('content', '')
            }

        if self.history and self.history[0]['role'] == 'system':
            self.history[0]['content'] = self.get_system_message()
        
        # Display loaded session
        self.open_output_view()
        self.append_message("\n[Loaded session: {}]\n".format(session_id))
        self.append_message("[Created: {}]\n".format(session_data.get('created_at', 'unknown')))
        self.append_message("[Messages: {}]\n".format(
            session_data.get('metadata', {}).get('message_count', 0)
        ))
        
        if self.active_model:
            self.append_message("[Model: {}]\n\n".format(self.active_model))
        
        # Display conversation history
        for msg in self.history:
            if msg['role'] == 'system':
                continue
            
            prefix = "\n--------\n# Q:  " if msg['role'] == 'user' else ""
            content = msg['content']
            
            if msg['role'] == 'user':
                self.append_message("{}{}\n\n".format(prefix, content))
            else:
                self.append_message("{}\n\n".format(content))
        
        self.update_status_bar()

    def show_session_list(self, action='info'):
        """Show list of available sessions"""
        sessions = SessionManager.list_sessions(self.window)
        
        if not sessions:
            self.append_message("\n[No saved sessions]\n")
            if action == 'load':
                self.show_input_panel()
            return
        
        if action == 'info':
            # Just display info
            self.open_output_view()
            self.append_message("\n==== [Saved Sessions]:\n")
            for session in sessions:
                current_marker = " (current)" if session['id'] == self.current_session_id else ""
                self.append_message("- {}{}\n".format(session['id'], current_marker))
                self.append_message("  Updated: {}\n".format(session.get('updated_at', 'unknown')))
                self.append_message("  Messages: {}, Model: {}\n".format(
                    session.get('message_count', 0),
                    session.get('model', 'unknown')
                ))
            self.append_message("\n")
            self.show_input_panel()
        
        elif action == 'load':
            # Show quick panel for selection
            items = []
            self.session_ids = []
            
            for session in sessions:
                self.session_ids.append(session['id'])
                current_marker = " (current)" if session['id'] == self.current_session_id else ""
                items.append([
                    "{}{}".format(session['id'], current_marker),
                    "Updated: {} | Messages: {} | Model: {}".format(
                        session.get('updated_at', 'unknown')[:19],
                        session.get('message_count', 0),
                        session.get('model', 'unknown')
                    )
                ])
            
            self.window.show_quick_panel(
                items,
                self.on_session_selected,
                sublime.MONOSPACE_FONT
            )

    def on_session_selected(self, index):
        """Handle session selection from quick panel"""
        if index == -1:
            self.show_input_panel()
            return
        
        session_id = self.session_ids[index]
        self.load_session(session_id)
        self.show_input_panel()

    def delete_session(self, session_id):
        """Delete a session"""
        if SessionManager.delete_session(session_id, self.window):
            self.append_message("\n[Session '{}' deleted]\n".format(session_id))
            if self.current_session_id == session_id:
                self.current_session_id = None
        else:
            self.append_message("\n[Session '{}' not found]\n".format(session_id))

    # Functions
    def discover_functions(self):
        """Discover available functions from User/DeepChatFunctions"""
        self.available_functions = {}
        
        user_path = os.path.join(sublime.packages_path(), 'User', 'DeepChatFunctions')
        
        if not os.path.exists(user_path):
            os.makedirs(user_path, exist_ok=True)
            return
        
        for filename in os.listdir(user_path):
            if not filename.endswith('.py'):
                continue
            
            file_path = os.path.join(user_path, filename)
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    code = f.read()
                
                namespace = {'sublime': sublime, 'sublime_plugin': sublime_plugin}
                exec(code, namespace)
                
                for item_name, item in namespace.items():
                    if item_name.startswith('_'):
                        continue
                    
                    # Handle command classes
                    if isinstance(item, type) and issubclass(item, sublime_plugin.WindowCommand):
                        # Convert DeepChatFnOpenFileCommand -> open_file
                        if item_name.startswith('DeepChatFn') and item_name.endswith('Command'):
                            func_name = item_name[10:-7]  # Strip prefix/suffix
                            # Convert CamelCase to snake_case
                            func_name = re.sub(r'(?<!^)(?=[A-Z])', '_', func_name).lower()
                            
                            doc = getattr(item, '__doc__', None) or 'No description'
                            self.available_functions[func_name] = {
                                'description': doc.strip(),
                                'type': 'command',
                                'class': item
                            }
                    
                    # Handle plain functions
                    elif callable(item):
                        doc = getattr(item, '__doc__', None) or 'No description'
                        self.available_functions[item_name] = {
                            'description': doc.strip(),
                            'type': 'function',
                            'callable': item
                        }
                        
            except Exception as e:
                print("Error loading function {}: {}".format(filename, e))

        if self.history and self.history[0]['role'] == 'system':
            self.history[0]['content'] = self.get_system_message()

    def get_functions_prompt(self):
        if not self.available_functions:
            return ""
        
        prompt = "\n\nYou have access to these functions:\n"
        for cmd_name, info in self.available_functions.items():
            prompt += "\n\n>>>> {}: {}".format(cmd_name, info['description'])
        
        prompt += "\n\nTo call a function, use this format:"
        prompt += "\n\n```"
        prompt += "\n<toolfunction_call>"
        prompt += "\n@command: function_name"
        prompt += "\nshort_arg_name: value"
        prompt += "\nmultiline_arg_name: <<<||"
        prompt += "\nmultiline value here"
        prompt += "\ncan have any characters"
        prompt += "\n||>>>"
        prompt += "\n</toolfunction_call>"
        prompt += "\n```"

        prompt += "\nIMPORTANT: function calls are executed after your turn. You MUST cut the answer and wait for user input after a call."
        prompt += "\nIMPORTANT: Make sure if the call is read-only. If it's a read-only call, use <wait_function_return/> to request user to skip next input."
        prompt += "\nRequest confirmation before create or edit files. Stop tool function attempts after 3 failures."
        
        return prompt

    def parse_function_calls(self, text):
        """Parse simple key-value format instead of JSON"""
        pattern = r'<toolfunction_call>(.*?)</toolfunction_call>'
        matches = re.findall(pattern, text, re.DOTALL)
        
        calls = []
        for match in matches:
            try:
                call_data = self._parse_kv_format(match.strip())
                calls.append(call_data)
            except Exception as e:
                print("Failed to parse function call: {}".format(e))
        
        return calls

    def _parse_kv_format(self, text):
        """Parse key-value format with multiline support"""
        lines = text.split('\n')
        result = {'args': {}}
        current_key = None
        multiline_content = []
        in_multiline = False
        
        for line in lines:
            if not line:
                if in_multiline:
                    multiline_content.append('')
                continue
            
            # Check for multiline delimiters
            if line == '<<<||':
                in_multiline = True
                multiline_content = []
                continue
            elif line == '||>>>':
                if current_key and in_multiline:
                    # Strip leading/trailing empty lines
                    while multiline_content and not multiline_content[0]:
                        multiline_content.pop(0)
                    while multiline_content and not multiline_content[-1]:
                        multiline_content.pop()
                    content = '\n'.join(multiline_content)
                    if current_key == '@command':
                        result['command'] = content
                    else:
                        result['args'][current_key] = content
                in_multiline = False
                current_key = None
                continue
            
            # Collect multiline content
            if in_multiline:
                multiline_content.append(line)
                continue
            
            # Parse key: value
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                
                if key == '@command':
                    result['command'] = value
                else:
                    # Handle multiline marker
                    if value == '<<<||':
                        current_key = key
                        in_multiline = True
                        multiline_content = []
                    else:
                        result['args'][key] = value
        
        return result

    def execute_function_call(self, call_data):
        """Execute a function call and return result"""
        func_name = call_data.get('command')
        args = call_data.get('args', {})
        
        if func_name not in self.available_functions:
            return {'success': False, 'error': 'Unknown function: {}'.format(func_name)}
        
        func_info = self.available_functions[func_name]
        
        try:
            if func_info['type'] == 'command':
                # Instantiate and run command
                cmd = func_info['class'](self.window)
                result = cmd.run(**args)
            else:
                # Call plain function
                result = func_info['callable'](self.window, **args)
            
            if isinstance(result, dict):
                return result
            else:
                return {'success': True, 'result': result}
                
        except Exception as e:
            return {'success': False, 'error': str(e), 'command': func_name}

    def process_response_with_functions(self, response_text):
        """Process LLM response, execute functions, return results"""
        function_calls = self.parse_function_calls(response_text)
        
        if not function_calls:
            return None
        
        results = []
        for call in function_calls:
            result = self.execute_function_call(call)
            results.append(result)
        
        return results

    # Model management
    def set_active_model_from_command(self, model_name):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        available_models = settings.get('models', {})

        if model_name in available_models:
            self.active_model = model_name
            self.save_last_model(model_name)
            if not self.result_view:
                self.find_output_view()
            if self.result_view:
                self.result_view.run_command('append', 
                    {'characters': "\n[Model set to: {}]\n".format(model_name)})
        else:
            if self.result_view:
                self.result_view.run_command('append', 
                    {'characters': "\n[Error]: Model '{}' not found in settings.\n".format(model_name)})
        
        self.update_status_bar()

    def set_active_model(self, model_name):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        available_models = settings.get('models', {})

        if model_name in available_models:
            self.active_model = model_name
            self.save_last_model(model_name)
            self.result_view.run_command('append', 
                {'characters': "\n[Model set to: {}]\n".format(model_name)})
        else:
            self.result_view.run_command('append', 
                {'characters': "\n[Error]: Model '{}' not found in settings.\n".format(model_name)})
        
        self.update_status_bar()

    def show_model_list(self):
        self.open_output_view()
        settings = sublime.load_settings('DeepChat.sublime-settings')
        available_models = settings.get('models', {})
        model_list_text = "\n==== [Available Models]:\n"
        
        for model_name, model_config in available_models.items():
            model_list_text += "- {}:   {}\n".format(
                model_name, model_config.get('description', '...'))
        
        model_list_text += "\n"
        self.result_view.run_command('append', {'characters': model_list_text})

    def show_current_model(self):
        if not self.result_view:
            return

        if self.active_model:
            self.result_view.run_command('append', 
                {'characters': "\n[Current Model: {}]\n".format(self.active_model)})
        else:
            self.result_view.run_command('append', 
                {'characters': "\n[Using default model. /list to show models]\n"})

    # API communication
    def send_message_with_retry(self, max_retries=3):
        """Send message with retry logic"""
        settings = sublime.load_settings('DeepChat.sublime-settings')
        model_to_use = self.active_model or settings.get('default_model', 'deepseek-chat')
        available_models = settings.get('models', {})
        model_config = available_models.get(model_to_use)

        if not model_config:
            sublime.error_message("Configuration for model '{}' not found.".format(model_to_use))
            return

        request = self._prepare_request(model_config)
        if not request:
            return

        stream = model_config.get('stream', False)
        
        # Display user message
        formatted_message = "\n--------\n# Q [{}]:  {}\n\n".format(self.message_id, self.user_message)
        self.result_view.run_command('append', {'characters': formatted_message})
        
        # Run in background thread
        thread = threading.Thread(
            target=self._send_message_thread,
            args=(request, stream, max_retries)
        )
        thread.daemon = True
        thread.start()
    
    
    def _send_message_thread(self, request, stream, max_retries):
        """Background thread for sending messages"""
        for attempt in range(max_retries):
            try:
                if stream:
                    self.setup_streaming()
                    self._stream_response_sync(request)
                else:
                    self._handle_non_streaming_response_sync(request)
                return  # Success
                
            except (urllib.error.URLError, socket.error, urllib.error.HTTPError) as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    msg = "\n[Connection error, retrying in {}s... (attempt {}/{}): {}]\n".format(
                        wait_time, attempt + 1, max_retries, str(e))
                    sublime.set_timeout(lambda m=msg: self.append_message(m), 0)
                    time.sleep(wait_time)
                else:
                    error_msg = "\n[Connection failed after {} attempts: {}]\n".format(
                        max_retries, str(e))
                    sublime.set_timeout(lambda m=error_msg: self.append_message(m), 0)
                    return

    def _prepare_request(self, model_config):
        """Prepare the API request"""        
        # Check request size limit
        settings = sublime.load_settings('DeepChat.sublime-settings')
        max_tokens = settings.get('max_request_tokens', 100000)
        
        # Rough token estimation: 1 token ≈ 4 chars
        total_chars = sum(len(msg.get('content', '')) for msg in self.history)
        estimated_tokens = total_chars // 4
        
        if estimated_tokens > max_tokens:
            error_msg = "\n[Error: Request too large (~{} tokens, limit: {}). Use /clear or /new to start fresh]\n".format(
                estimated_tokens, max_tokens
            )
            sublime.set_timeout(lambda: self.append_message(error_msg), 0)
            return None
        
        api_key = model_config.get('api_key', None)
        url = model_config.get("url", None)

        if not api_key:
            sublime.error_message("API key not set. Please add your API key to DeepChat.sublime-settings.")
            return None

        if not url:
            sublime.error_message("API URL not set")
            return None

        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer {}'.format(api_key)
        }

        data_dict = {
            "model": model_config.get("name", self.active_model),
            "messages": self.history,
            "max_tokens": model_config.get('max_tokens', 100),
            "temperature": model_config.get('temperature', 0.1),
            "stream": model_config.get('stream', False),
        }

        data_dict.update(model_config.get("extra", {}))

        if model_config.get("name", self.active_model) == "deepseek-reasoner":
            del data_dict["temperature"]

        data_json = json.dumps(data_dict)
        data_bytes = data_json.encode('utf-8')
        return urllib.request.Request(url, data_bytes, headers)

    def send_message(self):
        """Entry point - delegates to retry mechanism"""
        self.stopping = False
        self.send_message_with_retry()


    def setup_streaming(self):
        self.response_buffer = b''
        self.parse_buffer = b''
        self.reply = ''
        self.response_complete = False
        self.timer_running = False
        self.previous_reply_length = 0
        self.partial_json = ""

    def _handle_non_streaming_response_sync(self, request):
        """Handle non-streaming response - raises exceptions for retry"""
        with urllib.request.urlopen(request, timeout=30) as response:
            response_bytes = response.read()
            response_str = response_bytes.decode('utf-8')
            response_json = json.loads(response_str)
            choices = response_json.get('choices', [])
            
            if choices:
                reply = choices[0].get('message', {}).get('content', 'No reply from the API.')
                function_results = self.process_response_with_functions(reply)
                clean_reply = re.sub(
                    r'<toolfunction_call>(.{0,100}).*?</toolfunction_call>', 
                    r'<toolfunction_call>\1...</toolfunction_call>', 
                    reply, 
                    flags=re.DOTALL
                ).strip()
                self.add_message_to_history('assistant', clean_reply)

                if function_results:
                    results_text = "\n\nFunction execution results:\n{}".format(
                        json.dumps(function_results, indent=2)
                    )
                    self.history.append({'role': 'system', 'content': results_text})

                self.auto_save_session()
                
                # Check for auto-continue
                self.reply = reply
                if self._check_auto_continue():
                    sublime.set_timeout(lambda: self.display_response(self.user_message, reply), 0)
                    sublime.set_timeout(lambda: self._trigger_auto_continue(), 500)
                    return
            else:
                reply = 'No reply from the API.'
            
            sublime.set_timeout(lambda: self.display_response(self.user_message, reply), 0)

    def handle_non_streaming_response(self, request):
        """Legacy wrapper - no longer used"""
        try:
            self._handle_non_streaming_response_sync(request)
        except urllib.error.HTTPError as e:
            sublime.error_message("HTTP Error: {} - {}".format(e.code, e.reason))
        except urllib.error.URLError as e:
            sublime.error_message("URL Error: {}".format(e.reason))
        except Exception as e:
            sublime.error_message("An error occurred: {}".format(str(e)))

    # Streaming response handling
    def stream_response_with_retry(self, request, max_retries=3):
        """Stream response with retry logic"""
        for attempt in range(max_retries):
            try:
                self.stream_response(request)
                return
            except (urllib.error.URLError, socket.error) as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    sublime.set_timeout(lambda: self.append_message(
                        "\n[Connection error, retrying in {}s...]\n".format(wait_time)
                    ), 0)
                    time.sleep(wait_time)
                else:
                    sublime.set_timeout(lambda: self.append_message(
                        "\n[Connection failed after {} attempts: {}]\n".format(max_retries, str(e))
                    ), 0)
                    return

    def _stream_response_sync(self, request):
        """Stream response synchronously - raises exceptions for retry"""
        self.reply = ''
        self.previous_reply_length = 0
        self.last_update_time = time.time()
        self.response_watchdog_active = True
        
        watchdog_thread = threading.Thread(target=self._stream_watchdog)
        watchdog_thread.daemon = True
        watchdog_thread.start()
        
        # This will raise exceptions to be caught by retry mechanism
        with urllib.request.urlopen(request, timeout=30) as response:
            self.parse_buffer = b''
            
            while True and not self.stopping:
                self._safely_set_timeout(response, 5)
                chunk = response.read(1024)
                self.last_update_time = time.time()
                
                if not chunk:
                    self._process_buffer(final=True)
                    break
                
                self.parse_buffer += chunk
                self._process_buffer()
                
                if not self.timer_running:
                    sublime.set_timeout(self.update_view, 100)
                    self.timer_running = True
        
        # Finalize
        self.response_watchdog_active = False
        self._process_partial_json()
        
        if self.reply:
            function_results = self.process_response_with_functions(self.reply)
            self.response_complete = True
            clean_reply = re.sub(
                r'<toolfunction_call>(.{0,150}).*?</toolfunction_call>', 
                r'<toolfunction_call>\1...</toolfunction_call>', 
                self.reply, 
                flags=re.DOTALL
            ).strip()
            self.history.append({'role': 'assistant', 'content': clean_reply})
            
            if function_results:
                # Show execution results after response
                function_calls = self.parse_function_calls(self.reply)
                for i, result in enumerate(function_results):
                    call = function_calls[i]
                    if result['success']:
                        sublime.set_timeout(lambda c=call: self.append_message("\n[Executed: {}]\n".format(c.get('command'))), 0)
                    else:
                        sublime.set_timeout(lambda c=call, r=result: self.append_message("\n[Error: {} - {}]\n".format(c.get('command'), r.get('error'))), 0)
                
                results_text = "\n\nFunction execution results:\n{}".format(
                    json.dumps(function_results, indent=2)
                )
                self.history.append({'role': 'system', 'content': results_text})

            self.auto_save_session()
            sublime.set_timeout(lambda: self.update_view(final=True), 0)
            sublime.set_timeout(lambda: self._ensure_complete_update(), 300)
            
            # Check for auto-continue
            if self._check_auto_continue():
                sublime.set_timeout(lambda: self._trigger_auto_continue(), 500)
                
    def _process_buffer(self, final=False):
        if b'\n' in self.parse_buffer:
            lines = self.parse_buffer.split(b'\n')
            self.parse_buffer = lines.pop()
            
            for line in lines:
                self._process_line(line)
        elif final and self.parse_buffer:
            self._process_line(self.parse_buffer)
            self.parse_buffer = b''

    def _process_line(self, line):
        if not line.strip():
            return
        
        try:
            line_str = line.decode('utf-8', errors='replace').strip()
            
            if line_str.startswith('data: '):
                if line_str == "data: [DONE]":
                    return
                
                json_str = line_str[6:]
                self._handle_json_content(json_str)
            
            elif line_str.startswith('{'):
                self._handle_json_content(line_str)
                
        except Exception as e:
            print("Error processing line: {}".format(str(e)))

    def _handle_json_content(self, json_str):
        try:
            data = json.loads(json_str)
            self._extract_content(data)
            
        except ValueError:
            self.partial_json += json_str
            self._process_partial_json()

    def _process_partial_json(self):
        if not self.partial_json:
            return
            
        pattern = r'(\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
        matches = re.findall(pattern, self.partial_json)
        
        for match in matches:
            try:
                data = json.loads(match)
                self._extract_content(data)
                self.partial_json = self.partial_json.replace(match, '', 1)
            except ValueError:
                pass

    def _trigger_auto_continue(self):
        # Add a system message to prompt continuation
        continue_prompt = "[Continue from where you left off]"
        self.add_message_to_history('user', continue_prompt)
        self.user_message = continue_prompt
        
        # Send the continuation request
        self.send_message_with_retry()

    def _check_auto_continue(self):
        """Check if response ends with <wait_function_return/> tag"""
        if self.reply.strip().endswith('<wait_function_return/>'):
            return True
        return False

    def _extract_content(self, data):
        with self.content_lock:
            # OpenAI/compatible format
            if 'choices' in data:
                choices = data.get('choices', [])
                if choices and len(choices) > 0:
                    choice = choices[0]
                    
                    if 'delta' in choice:
                        delta = choice.get('delta', {})
                        if 'content' in delta and delta['content'] is not None:
                            self.reply += delta['content']
                    
                    elif 'message' in choice:
                        message = choice.get('message', {})
                        if 'content' in message and message['content'] is not None:
                            self.reply += message['content']
                    
                    elif 'text' in choice and choice['text'] is not None:
                        self.reply += choice['text']
            
            # Other API formats
            elif 'text' in data and data['text'] is not None:
                self.reply += data['text']
            
            elif 'content' in data and data['content'] is not None:
                self.reply += data['content']
                
            elif 'completion' in data and data['completion'] is not None:
                self.reply += data['completion']
                
            elif 'response' in data and data['response'] is not None:
                self.reply += data['response']

    def _stream_watchdog(self):
        while self.response_watchdog_active:
            time.sleep(1)
            
            elapsed = time.time() - self.last_update_time
            
            if elapsed > 15 and not self.response_complete:
                self.response_watchdog_active = False
                
                if self.reply:
                    sublime.set_timeout(lambda: self._handle_hang(), 0)
                return

    def _handle_hang(self):
        if not self.response_complete:
            self.reply += "\n\n[Response incomplete - stream timed out]"
            self.response_complete = True
            self.stopping = True
            self.add_message_to_history('assistant', self.reply)
            self.auto_save_session()
            self.update_view(final=True)

    def _safely_set_timeout(self, response, timeout=10):
        try:
            if hasattr(response, 'fp') and response.fp is not None:
                if hasattr(response.fp, 'raw') and response.fp.raw is not None:
                    if hasattr(response.fp.raw, '_sock') and response.fp.raw._sock is not None:
                        response.fp.raw._sock.settimeout(timeout)
        except Exception:
            pass

    def _ensure_complete_update(self):
        with self.content_lock:
            final_content = self.reply[self.previous_reply_length:]
            
        if final_content and self.result_view and self.result_view.is_valid():
            self.result_view.run_command('append', {'characters': final_content})
            self.previous_reply_length = len(self.reply)

    def update_view(self, final=False):
        try:
            if not self.result_view or not self.result_view.is_valid():
                self.response_complete = True
                self.timer_running = False
                return
                
            with self.content_lock:
                new_content = self.reply[self.previous_reply_length:]
                current_length = len(self.reply)
            
            if new_content:
                self.result_view.run_command('append', {'characters': new_content})
                
                with self.content_lock:
                    self.previous_reply_length = current_length
                    
                self.result_view.sel().clear()
                self.result_view.sel().add(sublime.Region(self.result_view.size()))
            
            if final:
                if not new_content.endswith('\n'):
                    self.result_view.run_command('append', {'characters': '\n'})
                self.timer_running = False
            else:
                delay = 30 if new_content else 100
                self.timer_running = True
                sublime.set_timeout(self.update_view, delay)
                
        except Exception as e:
            print("View update error: {}".format(e))
            if not final:
                sublime.set_timeout(self.update_view, 100)

    def display_response(self, user_message, reply):
        self.find_output_view()
        if not self.result_view:
            self.open_output_view()

        formatted_message = "{}\n\n".format(reply)
        self.result_view.run_command('append', {'characters': formatted_message})
        self.result_view.sel().clear()
        self.result_view.sel().add(sublime.Region(self.result_view.size()))
        self.window.focus_view(self.result_view)

    # Settings and configuration
    def get_agentic_hints(self):
        # Add package paths info
        packages_path = sublime.packages_path()
        user_path = os.path.join(packages_path, 'User')
        deepchat_path = os.path.join(packages_path, 'DeepChat')
        
        path_info = "\n\nUseful paths:\n"
        path_info += "- Sublime Text Packages root: {}\n".format(packages_path)
        path_info += "- Project folder: use window_summary function to find out\n"
        path_info += "- User folder: {}\n".format(user_path)
        path_info += "- DeepChat folder: {}\n".format(deepchat_path)
        path_info += "- DeepChatFunctions: {}\n".format(os.path.join(user_path, 'DeepChatFunctions'))
        path_info += "- DeepChatScripts: {}\\n".format(os.path.join(deepchat_path, 'scripts'))
        
        return self.get_functions_prompt() + path_info

    def get_system_message(self):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        base_message = settings.get('system_message', 'You are a helpful assistant.')
        output = base_message
        output += "\n"
        output += "VERY IMPORTANT: Don't try to make up answers if you don't know. Just say 'I don't know'.\n"
        output += "\n"
        output += self.get_agentic_hints()
        return output

    def load_last_model(self):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        self.active_model = settings.get('last_active_model', None)
        self.update_status_bar()

    def save_last_model(self, model_name):
        settings = sublime.load_settings('DeepChat.sublime-settings')
        settings.set('last_active_model', model_name)
        sublime.save_settings('DeepChat.sublime-settings')

    def update_status_bar(self):
        status_text = "deepchat:{}".format(self.active_model) if self.active_model else "deepchat:---"
        for view in self.window.views():
            view.set_status('deepchat_model', status_text)

