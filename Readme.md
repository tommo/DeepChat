
---

# DeepSeek Chat - Sublime Text Extension

A Sublime Text extension that integrates with the DeepSeek API to provide AI-powered chat functionality directly within the editor. This extension allows you to interact with the DeepSeek API, send messages, and receive responses in real-time, with support for streaming mode.

---

## Features

- **Chat with DeepSeek API**: Send messages and receive responses from the DeepSeek API.
- **Streaming Mode**: Enable streaming to receive responses in real-time, with updates every 100ms.
- **History Management**: Maintains a conversation history for context-aware interactions.
- **Customizable Settings**: Configure API key, model, system message, and other parameters.
- **Output View**: Displays the conversation in a dedicated view within Sublime Text.

---

## Installation

1. **Clone the Repository**:
   Clone this repository into your Sublime Text `Packages` directory.

   ```bash
   git clone https://github.com/yourusername/deepseek-chat-sublime.git
   ```

   Alternatively, you can download the repository as a ZIP file and extract it into the `Packages` directory.

2. **Install Dependencies**:
   Ensure you have Python 3 installed. The extension uses the `urllib` and `json` libraries, which are part of Python's standard library.

3. **Configure API Key**:
   Open the `DeepChat.sublime-settings` file in Sublime Text and add your DeepSeek API key:

   ```json
   {
       "api_key": "your_api_key_here",
       "model": "deepseek-chat",
       "system_message": "You are a helpful assistant.",
       "max_tokens": 100,
       "temperature": 0.7,
       "stream": true
   }
   ```

   Replace `your_api_key_here` with your actual API key.

---

## Usage

1. **Open the Command Palette**:
   Press `Ctrl+Shift+P` (Windows/Linux) or `Cmd+Shift+P` (Mac) to open the Command Palette.

2. **Start Chat**:
   Type `DeepSeek Chat` and select the command to start a chat session.

3. **Enter Your Message**:
   A input panel will appear at the bottom of the editor. Enter your message and press `Enter`.

4. **View Responses**:
   The response from the DeepSeek API will be displayed in a dedicated view named `DeepChatResult`. If streaming is enabled, the response will update in real-time.

5. **Exit Chat**:
   Type `/exit` in the input panel to end the chat session.

---

## Configuration

You can customize the extension by editing the `DeepChat.sublime-settings` file. Here are the available options:

| Key             | Description                                                                 | Default Value               |
|-----------------|-----------------------------------------------------------------------------|-----------------------------|
| `api_key`       | Your DeepSeek API key.                                                      | `""`                        |
| `model`         | The model to use for chat completions.                                      | `"deepseek-chat"`           |
| `system_message`| The system message to set the behavior of the assistant.                    | `"You are a helpful assistant."` |
| `max_tokens`    | The maximum number of tokens to generate in the response.                   | `100`                       |
| `temperature`   | Controls the randomness of the response (higher values = more random).      | `0.7`                       |
| `stream`        | Enable or disable streaming mode.                                           | `true`                      |

---

## Example

### Input
```plaintext
What is the capital of France?
```

### Output (Streaming Mode)
```plaintext
Q:  What is the capital of France?

A:  The capital of France is Paris.
```

---

## Troubleshooting

- **API Key Not Set**:
  Ensure that you have added your DeepSeek API key to the `DeepChat.sublime-settings` file.

- **Streaming Not Working**:
  Verify that the `stream` option is set to `true` in the settings file. Also, ensure that the API endpoint supports streaming.

- **Errors in Response**:
  Check the Sublime Text console (`View > Show Console`) for detailed error messages.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## Contributing

Contributions are welcome! If you find a bug or have a feature request, please open an issue or submit a pull request.

---

Enjoy chatting with DeepSeek directly in Sublime Text! 🚀

---

This `README.md` provides a comprehensive guide for users to install, configure, and use your Sublime Text extension. You can customize it further based on your specific needs or additional features.