import sublime
import sublime_plugin
import os
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

class ScriptRunner:
    """Handles execution of multi-step chat scripts"""
    
    def __init__(self, chat_command):
        self.chat = chat_command
        self.current_script = None
        self.current_step = 0
        self.script_vars = {}
        self.script_history = []
    
    @staticmethod
    def get_scripts_dirs(window=None):
        """Get list of script directories - both project and global"""
        dirs = []
        
        # Project scripts
        if window:
            folders = window.folders()
            if folders:
                project_dir = os.path.join(folders[0], '.deepchat', 'scripts')
                os.makedirs(project_dir, exist_ok=True)
                dirs.append(project_dir)
        
        # Global scripts
        user_dir = sublime.packages_path()
        global_dir = os.path.join(user_dir, 'User', 'DeepChat', 'scripts')
        os.makedirs(global_dir, exist_ok=True)
        dirs.append(global_dir)
        
        return dirs
    
    @staticmethod
    def list_scripts(window=None):
        """List available script files from both project and global directories"""
        scripts_dirs = ScriptRunner.get_scripts_dirs(window)
        scripts = []
        seen_names = set()
        
        for scripts_dir in scripts_dirs:
            if not os.path.exists(scripts_dir):
                continue
            print(scripts_dir)
            for filename in os.listdir(scripts_dir):
                if filename.endswith('.script.xml'):
                    file_path = os.path.join(scripts_dir, filename)
                    try:
                        tree = ET.parse(file_path)
                        root = tree.getroot()
                        name = root.get('name', filename)
                        
                        # Skip duplicates (project scripts override global)
                        if name in seen_names:
                            continue
                        seen_names.add(name)
                        
                        steps_elem = root.find('steps')
                        step_count = len(list(steps_elem)) if steps_elem is not None else 0
                        
                        scripts.append({
                            'name': name,
                            'description': root.get('description', ''),
                            'file_path': file_path,
                            'steps': step_count
                        })
                    except Exception as e:
                        print(e)
                        continue
        
        return scripts
    

    def load_script(self, script_path):
        """Load a script file"""
        try:
            tree = ET.parse(script_path)
            root = tree.getroot()
            
            # Parse variables
            variables = {}
            vars_elem = root.find('variables')
            if vars_elem is not None:
                for var in vars_elem.findall('var'):
                    variables[var.get('name')] = var.get('value', '')
            
            # Parse steps
            steps = []
            steps_elem = root.find('steps')
            if steps_elem is not None:
                steps = self._parse_steps(steps_elem)
            
            self.current_script = {
                'name': root.get('name', 'Unnamed'),
                'description': root.get('description', ''),
                'variables': variables,
                'steps': steps
            }
            self.current_step = 0
            self.script_vars = variables.copy()
            self.script_history = []
            
            return True
        except Exception as e:
            self.chat.append_message("\n[Error loading script: {}]\n".format(str(e)))
            return False
    
    def _parse_steps(self, parent):
        """Parse step elements recursively"""
        steps = []
        for elem in parent:
            step = {'type': elem.tag}
            
            # Copy attributes
            for key, value in elem.attrib.items():
                # Convert boolean strings
                if value.lower() in ('true', 'false'):
                    step[key] = value.lower() == 'true'
                else:
                    step[key] = value
            
            # Get text content for prompt
            if elem.tag == 'prompt' and elem.text:
                step['prompt'] = elem.text.strip()
            
            # Parse nested if_true/if_false for condition
            if elem.tag == 'condition':
                if_true = elem.find('if_true')
                if if_true is not None:
                    step['if_true'] = self._parse_steps(if_true)
                
                if_false = elem.find('if_false')
                if if_false is not None:
                    step['if_false'] = self._parse_steps(if_false)
            
            steps.append(step)
        
        return steps
    
    def execute_script(self):
        """Start executing the loaded script"""
        if not self.current_script:
            self.chat.append_message("\n[No script loaded]\n")
            return False
        
        self.chat.append_message("\n[Starting script: {}]\n".format(
            self.current_script.get('name', 'Unnamed')
        ))
        
        if self.current_script.get('description'):
            self.chat.append_message("[{}]\n".format(
                self.current_script.get('description')
            ))
        
        self.execute_next_step()
        return True
    
    def execute_next_step(self):
        """Execute the next step in the script"""
        steps = self.current_script.get('steps', [])
        
        if self.current_step >= len(steps):
            self.chat.append_message("\n[Script completed]\n")
            self.current_script = None
            return False
        
        step = steps[self.current_step]
        step_type = step.get('type', 'prompt')
        
        self.chat.append_message("\n[Step {}/{}]\n".format(
            self.current_step + 1, len(steps)
        ))
        
        if step_type == 'prompt':
            self._execute_prompt_step(step)
        elif step_type == 'function':
            self._execute_function_step(step)
        elif step_type == 'input':
            self._execute_input_step(step)
        elif step_type == 'condition':
            self._execute_condition_step(step)
        
        return True
    
    def _execute_prompt_step(self, step):
        """Execute a prompt step"""
        prompt = step.get('prompt', '')
        prompt = self._substitute_vars(prompt)
        
        if step.get('system'):
            self.chat.history.append({'role': 'system', 'content': prompt})
            self.chat.append_message("[System]: {}\n".format(prompt))
            self.current_step += 1
            self.execute_next_step()
        else:
            self.chat.history.append({'role': 'user', 'content': prompt})
            self.chat.user_message = prompt
            self.chat.append_message("\n# Q: {}\n\n".format(prompt))
            
            # Mark that we're in script mode
            self.chat.in_script_mode = True
            self.chat.send_message_with_retry()
    
    def _execute_function_step(self, step):
        """Execute a function call step"""
        func_name = step.get('function')
        args = step.get('args', {})
        
        # Substitute variables in args
        args = self._substitute_vars_in_dict(args)
        
        if func_name in self.chat.available_functions:
            call_data = {'command': func_name, 'args': args}
            result = self.chat.execute_function_call(call_data)
            
            # Store result in variables
            if step.get('store_as'):
                self.script_vars[step['store_as']] = result
            
            self.current_step += 1
            self.execute_next_step()
        else:
            self.chat.append_message("\n[Error: Unknown function {}]\n".format(func_name))
            self.current_script = None
    
    def _execute_input_step(self, step):
        """Execute an input step - pause for user input"""
        prompt = step.get('prompt', 'Enter value:')
        var_name = step.get('store_as')
        
        self.chat.append_message("\n[Input required: {}]\n".format(prompt))
        
        # Show input panel
        def on_input(value):
            if var_name:
                self.script_vars[var_name] = value
            self.current_step += 1
            self.execute_next_step()
        
        self.chat.window.show_input_panel(
            prompt, "", on_input, None, None
        )
    
    def _execute_condition_step(self, step):
        """Execute a conditional step"""
        condition = step.get('test', step.get('condition', ''))
        condition = self._substitute_vars(condition)
        
        try:
            # Simple eval - be careful with user input
            result = eval(condition, {"__builtins__": {}}, self.script_vars)
            
            if result:
                if_steps = step.get('if_true', [])
                self._inject_steps(if_steps)
            else:
                else_steps = step.get('if_false', [])
                self._inject_steps(else_steps)
            
            self.current_step += 1
            self.execute_next_step()
            
        except Exception as e:
            self.chat.append_message("\n[Condition error: {}]\n".format(str(e)))
            self.current_script = None
    
    def _inject_steps(self, steps):
        """Inject steps into current position"""
        if not steps:
            return
        
        current_steps = self.current_script.get('steps', [])
        current_steps[self.current_step:self.current_step] = steps
        self.current_script['steps'] = current_steps
    
    def _substitute_vars(self, text):
        """Substitute {{var}} patterns with values"""
        def replace(match):
            var_name = match.group(1)
            return str(self.script_vars.get(var_name, match.group(0)))
        
        return re.sub(r'\{\{(\w+)\}\}', replace, text)
    
    def _substitute_vars_in_dict(self, d):
        """Recursively substitute variables in dict"""
        result = {}
        for k, v in d.items():
            if isinstance(v, str):
                result[k] = self._substitute_vars(v)
            elif isinstance(v, dict):
                result[k] = self._substitute_vars_in_dict(v)
            elif isinstance(v, list):
                result[k] = [self._substitute_vars(x) if isinstance(x, str) else x for x in v]
            else:
                result[k] = v
        return result
    
    def on_response_complete(self, response):
        """Called when LLM response is complete"""
        if not self.current_script:
            return
        
        # Store response in history
        self.script_history.append({
            'step': self.current_step,
            'response': response
        })
        
        # Check if current step wants to store response
        steps = self.current_script.get('steps', [])
        if self.current_step < len(steps):
            step = steps[self.current_step]
            if step.get('store_as'):
                self.script_vars[step['store_as']] = response
        
        # Move to next step
        self.current_step += 1
        
        # Check for auto-continue
        if self.current_step < len(steps):
            next_step = steps[self.current_step]
            if next_step.get('auto_continue', True):
                sublime.set_timeout(lambda: self.execute_next_step(), 500)
            else:
                self.chat.append_message("\n[Script paused - type /continue to proceed]\n")
        else:
            self.chat.append_message("\n[Script completed]\n")
            self.current_script = None


class DeepChatRunScriptCommand(sublime_plugin.WindowCommand):
    """Command to run a chat script"""
    
    def run(self, script_path=None):
        if script_path:
            self._run_script(script_path)
        else:
            self._show_script_list()
    
    def _show_script_list(self):
        scripts = ScriptRunner.list_scripts(self.window)
        
        if not scripts:
            sublime.status_message("No scripts found")
            return
        
        self.scripts = scripts
        items = []
        
        for script in scripts:
            items.append([
                script['name'],
                "{} | {} steps".format(
                    script.get('description', 'No description'),
                    script.get('steps', 0)
                )
            ])
        
        self.window.show_quick_panel(
            items,
            self._on_script_selected,
            sublime.MONOSPACE_FONT
        )
    
    def _on_script_selected(self, index):
        if index == -1:
            return
        
        script_path = self.scripts[index]['file_path']
        self._run_script(script_path)
    
    def _run_script(self, script_path):
        # Get or create chat command instance
        chat_cmd = None
        for view in self.window.views():
            if view.name() == "DeepChatResult":
                # Find the chat command instance
                # This is a bit hacky - better to store it globally
                break
        
        # For now, just trigger chat with script parameter
        self.window.run_command("deep_seek_chat", {
            "command": "run_script",
            "script_path": script_path
        })
