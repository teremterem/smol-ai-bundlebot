import ast
import asyncio
import os
import sys
from uuid import uuid4

import promptlayer
import tiktoken
from dotenv import load_dotenv
from mergedbots import InMemoryBotManager, MergedBot
from mergedbots.experimental.sequential import ConversationSequence

from constants import DEFAULT_DIR, DEFAULT_MODEL, DEFAULT_MAX_TOKENS
from utils import clean_dir

load_dotenv()

promptlayer.api_key = os.environ["PROMPTLAYER_API_KEY"]

openai = promptlayer.openai
# Set up your OpenAI API credentials
openai.api_key = os.environ["OPENAI_API_KEY"]

bot_manager = InMemoryBotManager()


@bot_manager.create_bot("ResponseGenerator")
async def generate_response(bot: MergedBot, conv_sequence: ConversationSequence):
    # TODO think how to implement `concurrency_limit=5`
    # TODO wait for a "message bundle" of multiple messages (sys_prompt, user_prompt, args etc) ?
    #  or maybe employ some sort of "FormFillingBot" ?
    request = await conv_sequence.wait_for_incoming()
    user_prompt = request.content
    system_prompt = request.custom_fields.get("system_prompt")
    args = request.custom_fields.get("args") or []
    model = request.custom_fields.get("model") or DEFAULT_MODEL

    # IMPORTANT: Keep import statements here due to Modal container restrictions
    # https://modal.com/docs/guide/custom-container#additional-python-packages

    def reportTokens(prompt):
        encoding = tiktoken.encoding_for_model(model)
        # print number of tokens in light gray, with first 50 characters of prompt in green. if truncated, show that
        # it is truncated
        # TODO send this to the UserProxyBot
        print(
            "\033[37m" + str(len(encoding.encode(prompt))) + " tokens\033[0m" + " in prompt: " + "\033[92m" +
            prompt[:50] + "\033[0m" + ("..." if len(prompt) > 50 else "")
        )

    messages = []
    messages.append({"role": "system", "content": system_prompt})
    reportTokens(system_prompt)
    messages.append({"role": "user", "content": user_prompt})
    reportTokens(user_prompt)
    # Loop through each value in `args` and add it to messages alternating role between "assistant" and "user"
    role = "assistant"
    for value in args:
        messages.append({"role": role, "content": value})
        reportTokens(value)
        role = "user" if role == "assistant" else "assistant"

    params = {
        "model": model,
        "messages": messages,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": 0,
    }

    # Send the API request
    response = openai.ChatCompletion.create(**params)

    # Get the reply from the API response
    reply = response.choices[0]["message"]["content"]

    conv_sequence.yield_outgoing(await request.final_bot_response(bot, reply))


# @stub.function()
def generate_file(filename, model=DEFAULT_MODEL, filepaths_string=None, shared_dependencies=None, prompt=None):
    # call openai api with this prompt
    filecode = generate_response.call(
        model,
        f"""You are an AI developer who is trying to write a program that will generate code for the user based on \
their intent.
        
    the app is: {prompt}

    the files we have decided to generate are: {filepaths_string}

    the shared dependencies (like filenames and variable names) we have decided on are: {shared_dependencies}
    
    only write valid code for the given filepath and file type, and return only the code.
    do not add any other explanation, only return valid code for that file type.
    """,
        f"""
    We have broken up the program into per-file generation. 
    Now your job is to generate only the code for the file {filename}. 
    Make sure to have consistent filenames if you reference other files we are also generating.
    
    Remember that you must obey 3 things: 
       - you are generating code for the file {filename}
       - do not stray from the names of the files and the shared dependencies we have decided on
       - MOST IMPORTANT OF ALL - the purpose of our app is {prompt} - every line of code you generate must be valid \
code. Do not include code fences in your response, for example
    
    Bad response:
    ```javascript 
    console.log("hello world")
    ```
    
    Good response:
    console.log("hello world")
    
    Begin generating the code now.

    """,
    )

    return filename, filecode


@bot_manager.create_bot("SmolAI")
async def smol_ai(bot: MergedBot, conv_sequence: ConversationSequence):
    request = await conv_sequence.wait_for_incoming()
    prompt = request.content
    directory = request.custom_fields.get("directory") or DEFAULT_DIR
    model = request.custom_fields.get("model") or DEFAULT_MODEL
    file = request.custom_fields.get("file")

    # TODO send this to the UserProxyBot
    print("hi its me, 🐣the smol developer🐣! you said you wanted:")
    # print the prompt in green color
    print("\033[92m" + prompt + "\033[0m")

    # call openai api with this prompt
    filepaths_msg = await generate_response.bot.get_final_response(
        # TODO send this as a "message bundle"
        await bot.manager.create_originator_message(
            channel_type="bot-to-bot",
            channel_id=str(uuid4()),
            originator=bot,
            custom_fields={
                "model": model,
                "system_prompt": """You are an AI developer who is trying to write a program that will generate code \
for the user based on their intent.

When given their intent, create a complete, exhaustive list of filepaths that the user would write to make the \
program.

only list the filepaths you would write, and return them as a python list of strings. 
do not add any other explanation, only return a python list of strings.""",
            },
            content=prompt,
        )
    )
    filepaths_string = filepaths_msg.content

    # TODO send this to the UserProxyBot
    print(filepaths_string)

    # parse the result into a python list
    list_actual = []
    try:
        list_actual = ast.literal_eval(filepaths_string)

        # if shared_dependencies.md is there, read it in, else set it to None
        shared_dependencies = None
        if os.path.exists("shared_dependencies.md"):
            with open("shared_dependencies.md", "r") as shared_dependencies_file:
                shared_dependencies = shared_dependencies_file.read()

        if file is not None:
            # check file
            print("file", file)
            filename, filecode = generate_file(
                file,
                model=model,
                filepaths_string=filepaths_string,
                shared_dependencies=shared_dependencies,
                prompt=prompt,
            )
            write_file(filename, filecode, directory)
        else:
            clean_dir(directory)

            # understand shared dependencies
            shared_dependencies_msg = await generate_response.bot.get_final_response(
                # TODO send this as a "message bundle"
                await bot.manager.create_originator_message(
                    channel_type="bot-to-bot",
                    channel_id=str(uuid4()),
                    originator=bot,
                    custom_fields={
                        "model": model,
                        "system_prompt": f"""You are an AI developer who is trying to write a program that will \
generate code for the user based on their intent.

In response to the user's prompt:

---
the app is: {{prompt}}
---

the files we have decided to generate are: {filepaths_string}

Now that we have a list of files, we need to understand what dependencies they share.
Please name and briefly describe what is shared between the files we are generating, including exported \
variables, data schemas, id names of every DOM elements that javascript functions will use, message names, and \
function names.
Exclusively focus on the names of the shared dependencies, and do not add any other explanation.""",
                    },
                    content=prompt,
                )
            )
            shared_dependencies = shared_dependencies_msg.content

            # TODO send this to the UserProxyBot
            print(shared_dependencies)
            return
            # write shared dependencies as a md file inside the generated directory
            write_file("shared_dependencies.md", shared_dependencies, directory)

            # Iterate over generated files and write them to the specified directory
            for filename, filecode in generate_file.map(
                list_actual, order_outputs=False,
                kwargs=dict(model=model, filepaths_string=filepaths_string, shared_dependencies=shared_dependencies,
                            prompt=prompt)
            ):
                write_file(filename, filecode, directory)

    except ValueError:
        print("Failed to parse result")


def write_file(filename, filecode, directory):
    # Output the filename in blue color
    print("\033[94m" + filename + "\033[0m")
    print(filecode)

    file_path = os.path.join(directory, filename)
    _dir = os.path.dirname(file_path)

    # Check if the filename is actually a directory
    if os.path.isdir(file_path):
        print(f"Error: {filename} is a directory, not a file.")
        return

    os.makedirs(_dir, exist_ok=True)

    # Open the file in write mode
    with open(file_path, "w") as file:
        # Write content to the file
        file.write(filecode)


async def main():
    # Check for arguments
    if len(sys.argv) < 2:

        # Looks like we don't have a prompt. Check if prompt.md exists
        if not os.path.exists("prompt.md"):
            # Still no? Then we can't continue
            print("Please provide a prompt")
            sys.exit(1)

        # Still here? Assign the prompt file name to prompt
        prompt = "prompt.md"

    else:
        # Set prompt to the first argument
        prompt = sys.argv[1]

    # Pull everything else as normal
    directory = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DIR
    file = sys.argv[3] if len(sys.argv) > 3 else None

    # read file from prompt if it ends in a .md filetype
    if prompt.endswith(".md"):
        with open(prompt, "r") as promptfile:
            prompt = promptfile.read()

    user = await bot_manager.find_or_create_user(
        channel_type="cli",
        channel_specific_id="cli",
        user_display_name="User",
    )
    user_message = await bot_manager.create_originator_message(
        channel_type="cli",
        channel_id="cli",
        originator=user,
        content=prompt,
        custom_fields={
            # "model": "gpt-4",
            "directory": directory,
            "file": file,
        },
    )
    # Run the main function
    async for response in smol_ai.bot.fulfill(user_message):
        print(response.content)


if __name__ == "__main__":
    asyncio.run(main())
