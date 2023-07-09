
import logging
import inspect

from functools import wraps
from textwrap import dedent
from typing import Callable, List, Optional,  Union


from langchain import LLMChain,  PromptTemplate
from langchain.tools.base import BaseTool
from langchain.schema import BaseOutputParser
from langchain.llms.base import BaseLanguageModel

from promptwatch import register_prompt_template

from .schema import OutputWithFunctionCall

from .chains import LLMDecoratorChainWithFunctionSupport, LLMDecoratorChain



from .common import *
from .prompt_template import PromptDecoratorTemplate
from .output_parsers import *
from .streaming_context import StreamingContext


def llm_prompt(
        prompt_type:PromptTypeSettings=PromptTypes.UNDEFINED, # do not change the order of this first parameter unless you will change also the fist few lines... since we are handling cases when decorator is used with and without arguments too, than this will be the func
        template_format:str = "f-string-extra",
        output_parser:Union[str,None, BaseOutputParser]="auto", 
        stop_tokens:List[str]=None, 
        template_name:str=None, 
        template_version:str=None, 
        capture_stream:bool=None,
        llm:Optional[BaseLanguageModel]=None,
        format_instructions_parameter_key:str="FORMAT_INSTRUCTIONS",
        retry_on_output_parsing_error:bool=True,
        verbose:bool=None,
        expected_gen_tokens:Optional[int]=None,
        llm_selector_rule_key:Optional[str]=None,
        ):
    """
    Decorator for functions that turns a regular function into a LLM prompt executed with default model and settings.
    
    This can be applied on any function that has a docstring with a prompt template. 
    If the function is async, the prompt will be executed asynchronously (with all the langchain async infrastructure).

    Note that the code of the function will never be executed... 

    Args:
        `prompt_type`: (Optional[PromptTypeSettings]) - This allows you mark your prompt with one of the predefined prompt types (see PromptTypes class - but you can subclass it!) to predefine some settings like LLM or style and color of logging into console.

        `template_format` (Optional[str]): one of [ `f-string` | `f-string-extra` ] ... f-string-extra is a superset of f-string template formats, enabling for optional sections.

        `output_parser` (Optional[str]): one of [ `auto` | `json` | `str` | `list` ] or `None` or langchain OutputParser object - you can control how will the output be parsed. 
        
            `auto` - default - determine the output type automatically based on output type annotations

            `str` or `None` - will return plain string output

            `list` - will parse bullet or numbered list (each item on a new line) as a list

            `boolean` - will parse the output as boolean. Expects clear Yes/No in the output

            `json` - will parse the output as json

            `functions` - will use the OpenAI functions to generate the output in desired format ... only for pydantic models and ChatOpenAI model

            `markdown` - will parse the output as markdown sections, the name of each section will be returned as a key and the content as a value. For nested sections, the value will be a dict with the same structure.

            `pydantic` - will parse the output as json and then convert into a pydantic model


        `stop_tokens` (Optional[List[str]]): list of stop tokens to instruct the LLM to stop generating text when it encounters any of these tokens. If not provided, the default stop tokens of the LLM will be used.

        `format_instructions_parameter_key` - name of the format instructions parameter - this will enable you to include the instructions on how LLM should format the output, generated by the output_parsers 
        ... if you include this into your prompt (docs), you don't need to reinvent the formatting instructions. 
        This works pretty well if you have an annotated pydantic model as an function output. If you are expecting a dict, you should probably include your own formatting instructions, since there is not much to infer from a dict structure.

        `retry_on_output_parsing_error` - whether to try to re-format the output if the output parser fails to parse the output by another LLM call

        `verbose` - whether to print the response from LLM into console

        `expected_gen_tokens` - hint for LLM selector ... if not set, default values of the LLM selector will be used (usually 1/3 of the prompt length)

        `llm_selector_rule_key` - key of the LLM selector rule to use ... if set, only LLMs with assigned rule with this key will be considered. You can also use llm_selector_rule_key argument when calling the llm_prompt function to override the default rule key. 
    """
    


    if  callable(prompt_type):
        # this is the case when the decorator is called without arguments
        # we initialize params with default values
        func = prompt_type
        prompt_type = PromptTypes.UNDEFINED
    else:
        func = None
    
    if verbose is None:
        verbose = GlobalSettings.get_current_settings().verbose
    
    if verbose:
        if prompt_type:
            prompt_type = prompt_type.as_verbose()
        else:
            prompt_type = PromptTypeSettings(color=LogColors.DARK_GRAY,log_level=100, capture_stream=capture_stream)
            
    
    
    def decorator(func):
        prompt_str = dedent(func.__doc__)
        name=func.__name__
        full_name=f"{func.__module__}.{name}" if func.__module__!="__main__" else name
        is_async = inspect.iscoroutinefunction(func)
        _llm_selector_rule_key=llm_selector_rule_key

        

        if prompt_type:
            _capture_stream = prompt_type.capture_stream if capture_stream is None else capture_stream
        else:
            _capture_stream = capture_stream
        if _capture_stream and not is_async:
            print_log(f"Warning: capture_stream=True is only supported for async functions. Ignoring capture_stream for {full_name}", logging.WARNING, LogColors.YELLOW)
            _capture_stream=False
            
                
                
       

        def prepare_call_args(*args, **kwargs):
            global_settings = GlobalSettings.get_current_settings()

            capture_stream=_capture_stream

            if "capture_stream" in kwargs:
                if not isinstance(capture_stream,bool):
                    raise ValueError("capture_stream is a reserved kwarg and must be of type bool")
                capture_stream=kwargs["capture_stream"]
                del kwargs["capture_stream"]

            if capture_stream and not StreamingContext.get_context():
                print_log(f"INFO: Not inside StreamingContext. Ignoring capture_stream for {full_name}", logging.DEBUG, LogColors.WHITE)
                capture_stream=False
            
            
            if not llm:
                if prompt_type and prompt_type.llm_selector:
                    llm_selector= prompt_type.llm_selector
                else:
                    llm_selector=  global_settings.llm_selector 

                if capture_stream and not llm_selector:
                    if not global_settings.default_streaming_llm:
                        print_log(f"Warning: capture_stream on {name} is on, but the default LLM {llm} doesn't seem to be supporting streaming.", logging.WARNING, LogColors.YELLOW)
                        
                    prompt_llm=global_settings.default_streaming_llm or global_settings.default_llm
                else:
                    prompt_llm = global_settings.default_llm

                if kwargs.get("llm_selector_rule_key"):
                    llm_selector_rule_key=kwargs["llm_selector_rule_key"]
                    del kwargs["llm_selector_rule_key"]
                else:
                    llm_selector_rule_key=_llm_selector_rule_key
                
            else:
                prompt_llm=llm
                llm_selector=None # if LLM is explicitly provided, we don't use the selector
                if capture_stream:
                    if  hasattr(llm,"streaming"):
                        if not getattr(llm, "streaming"):
                            print_log(f"Warning: capture_stream on {name} is on, but the provided LLM {llm} doesn't have streaming on! Stream wont be captured", logging.WARNING, LogColors.YELLOW)
                    else:
                        print_log(f"Warning: capture_stream on {name} is on, but the provided LLM {llm} doesn't seem to be supporting streaming.", logging.WARNING, LogColors.YELLOW)
                   
                

            input_variables_source=None
            if len(args)==1 and hasattr(args[0],"__dict__"):
                # is a proper object
                input_variables_source = args[0]

            elif len(args)>1:
                raise Exception(f"Positional arguments are not supported for prompt functions. Only one positional argument as an object with attributes as a source of inputs is supported. Got: {args}")
            
            
            prompt_template = PromptDecoratorTemplate.from_func(func, 
                                                            template_format=template_format, 
                                                            output_parser=output_parser, 
                                                            format_instructions_parameter_key=format_instructions_parameter_key,
                                                            template_name=template_name,
                                                            template_version=template_version,
                                                            prompt_type=prompt_type,
                                                            )
            if prompt_template.default_values:
                kwargs = {**prompt_template.default_values, **kwargs}

            if "callbacks" in kwargs:
                callbacks=kwargs.pop("callbacks")
            else:
                callbacks=[]
            
            if capture_stream:
                callbacks.append(StreamingContext.StreamingContextCallback())
            

            if "memory" in kwargs:
                memory = kwargs.pop("memory")
            else:
                memory=None

            if "functions" in kwargs:
                functions=kwargs.pop("functions")
            else:
                functions=None

                
            if functions:
                llmChain = LLMDecoratorChainWithFunctionSupport(llm=prompt_llm, prompt=prompt_template,  memory=memory, functions=functions, llm_selector=llm_selector, capture_stream=capture_stream, expected_gen_tokens=expected_gen_tokens, llm_selector_rule_key=llm_selector_rule_key )
            elif isinstance(prompt_template.output_parser, OpenAIFunctionsPydanticOutputParser):
                function=prompt_template.output_parser.build_llm_function()
                kwargs["function_call"] = function
                llmChain = LLMDecoratorChainWithFunctionSupport(llm=prompt_llm, prompt=prompt_template,  memory=memory, functions=[function], llm_selector=llm_selector, capture_stream=capture_stream, expected_gen_tokens=expected_gen_tokens, llm_selector_rule_key=llm_selector_rule_key  )
            else:
                llmChain = LLMDecoratorChain(llm=prompt_llm, prompt=prompt_template,  memory=memory, llm_selector=llm_selector, capture_stream=capture_stream, expected_gen_tokens=expected_gen_tokens, llm_selector_rule_key=llm_selector_rule_key )
            other_supported_kwargs={"stop","callbacks","function_call"}
            unexpected_inputs = [key for key in kwargs if key not in prompt_template.input_variables and key not in other_supported_kwargs ]
            if unexpected_inputs:
                raise TypeError(f"Unexpected inputs for prompt function {full_name}: {unexpected_inputs}. \nValid inputs are: {prompt_template.input_variables}\nHint: Make sure that you've used all the inputs in the template")
            
            missing_inputs = [key for key in prompt_template.input_variables if key not in kwargs ]
            if format_instructions_parameter_key in missing_inputs:
                missing_inputs.remove(format_instructions_parameter_key)
                kwargs[format_instructions_parameter_key]=None #init the format instructions with None... will be filled later
            if memory and memory.memory_key in missing_inputs:
                missing_inputs.remove(memory.memory_key)

            if missing_inputs:
                if input_variables_source:
                    missing_value={}
                    for key in missing_inputs:
                        value= getattr(input_variables_source, key,missing_value)
                        if value is missing_value:
                            raise TypeError(f"Missing a input for prompt function {full_name}: {key}.")
                        else:
                            kwargs[key] = value


                        
                else:
                    raise TypeError(f"{full_name}: missing 1 required keyword-only argument: {missing_inputs}")
                
            
            if stop_tokens:
                kwargs["stop"]=stop_tokens
            call_args = {"inputs":kwargs, "return_only_outputs":True, "callbacks":callbacks}
           
            return llmChain, call_args
        
        
        
        def get_retry_parse_call_args(prompt_template:PromptDecoratorTemplate, exception:OutputParserExceptionWithOriginal, get_original_prompt:Callable):
            logging.warning(msg=f"Failed to parse output for {full_name}: {exception}\nRetrying...")
            if format_instructions_parameter_key not in prompt_str:
                logging.warning(f"Please note that we didn't find a {format_instructions_parameter_key} parameter in the prompt string. If you don't include it in your prompt template, you need to provide your custom formatting instructions.")    
            if exception.original_prompt_needed_on_retry:
                original_prompt=get_original_prompt()
            else:
                original_prompt=""
            retry_parse_template = PromptTemplate.from_template("{original_prompt}This is our original response {original} but it's not in correct format, please convert it into following format:\n{format_instructions}\n\nIf the response doesn't seem to be relevant to the expected format instructions, return 'N/A'")
            register_prompt_template("retry_parse_template", retry_parse_template)
            prompt_llm=llm or GlobalSettings.get_current_settings().default_llm
            retryChain = LLMChain(llm=prompt_llm, prompt=retry_parse_template)
            format_instructions = prompt_template.output_parser.get_format_instructions()
            if not format_instructions:
                raise Exception(f"Failed to get format instructions for {full_name} from output parser {prompt_template.output_parser}.")
            call_kwargs = {"original_prompt":original_prompt, "original":exception.original, "format_instructions":format_instructions}
            return retryChain, call_kwargs
        
        def process_results(llmChain, result_data, result, is_function_call):
            log_results(result_data, result, is_function_call, verbose, prompt_type)
            if llmChain.prompt.output_parser:    
                if isinstance(llmChain.prompt.output_parser, OpenAIFunctionsPydanticOutputParser):
                    # there is no result probably, but if there is, we ignore it... we are interested only in tha data in function_call_info
                    result = llmChain.prompt.output_parser.parse(result_data["function_call_info"]["arguments"])
                    result_data.pop("function_call_info") # we don't need it anymore, and later in the code we check it its here to create OutputWithFunctionCall
                    result_data.pop("function")

                else:
                    if result:
                        result = llmChain.prompt.output_parser.parse(result)
            return result

        if not is_async:

            @wraps(func)
            def wrapper(*args, **kwargs):
                
                print_log(log_object=f"> Entering {name} prompt decorator chain", log_level=prompt_type.log_level if prompt_type else logging.DEBUG,color=LogColors.WHITE_BOLD)
                llmChain, chain_args = prepare_call_args(*args, **kwargs)

                try:
                    result_data = llmChain(**chain_args)

                    result = result_data[llmChain.output_key]
                    is_function_call = result_data.get("function_call_info")
                    result = process_results(llmChain, result_data, result,is_function_call)
                        
                    
                except OutputParserException as e:
                    if retry_on_output_parsing_error and isinstance(e, OutputParserExceptionWithOriginal):
                        prompt_template = llmChain.prompt 
                        retryChain, call_kwargs = get_retry_parse_call_args(prompt_template, e, lambda: llmChain.prompt.format(**chain_args))
                        result = retryChain.predict(**call_kwargs)
                        if verbose or prompt_type:
                            print_log(log_object=f"\nResult:\n{result}", log_level=prompt_type.log_level if not verbose else 100,color=prompt_type.color if prompt_type else LogColors.BLUE)
                        parsed = prompt_template.output_parser.parse(result)
                        return parsed
                    else: 
                        raise e

                print_log(log_object=f"> Finished chain", log_level=prompt_type.log_level if prompt_type else logging.DEBUG,color=LogColors.WHITE_BOLD)
                if "function_call_info" in result_data:
                    return _generate_output_with_function_call(result=result, result_data=result_data, verbose=verbose,callbacks=kwargs.get("callbacks"))
                return result

            

            
            return wrapper
        
        else:
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                
                
                print_log(log_object=f"> Entering {name} prompt decorator chain", log_level=prompt_type.log_level if prompt_type else logging.DEBUG,color=LogColors.WHITE_BOLD)
                llmChain, chain_args = prepare_call_args(*args, **kwargs)
                
                try:
                    
                    result_data = await llmChain.acall(**chain_args)
                    result = result_data[llmChain.output_key]
                    is_function_call = result_data.get("function_call_info")
                    result = process_results(llmChain, result_data, result,is_function_call)
                    
                    
                except OutputParserException as e:
                    if retry_on_output_parsing_error and isinstance(e, OutputParserExceptionWithOriginal):
                        prompt_template = llmChain.prompt 
                        retryChain, call_kwargs = get_retry_parse_call_args(prompt_template, e, lambda: llmChain.prompt.format(**chain_args))
                        result = await retryChain.apredict(**call_kwargs)
                        if verbose or prompt_type:
                            print_log(log_object=f"\nResult:\n{result}", log_level=prompt_type.log_level if not verbose else 100,color=prompt_type.color if prompt_type else LogColors.BLUE)
                        parsed = prompt_template.output_parser.parse(result)
                        return parsed
                    else: 
                        raise e

                print_log(log_object=f"> Finished chain", log_level=prompt_type.log_level if prompt_type else logging.DEBUG,color=LogColors.WHITE_BOLD)
                if "function_call_info" in result_data:
                    return _generate_output_with_function_call(result=result, result_data=result_data, verbose=verbose,callbacks=kwargs.get("callbacks"))
                return result
            return async_wrapper
    if func:
        return decorator(func)
    else:
        return decorator




def _generate_output_with_function_call(result:Any, result_data:dict, verbose, callbacks):
    """ get parsed result, function call data from llm and list of functions and build  OutputWithFunctionCall """
    # find the function first:
    
    _function = result_data["function"]
    if result_data.get("function_call_info"):
        _tool_arguments = result_data["function_call_info"]["arguments"]
        if isinstance(_function, BaseTool):
            # langchain hack >> "__arg1" as a single argument hack
            _is_single_arg_hack="__arg1" in _tool_arguments and len(_tool_arguments)==1
            tool_input= _tool_arguments["__arg1"] if _is_single_arg_hack else _tool_arguments
            _tool_arguments = tool_input
            def _sync_function(arguments=tool_input):
                return _function.run(tool_input=arguments, verbose=verbose, callbacks=callbacks)
            
            async def _async_function(arguments=tool_input):
                return await _function.arun(tool_input=arguments, verbose=verbose, callbacks=callbacks)
                
            

        elif callable(_function):
            # TODO: add support for verbose and callbacks
            
            is_async = inspect.iscoroutinefunction(_function)
            
            if is_async:
                _async_function = _function
                _sync_function = None
            else:
                _sync_function = _function
                _async_function = None
        else:
            raise TypeError(f"Invalid function type: {_function} of type {type(_function)}")

        return OutputWithFunctionCall(
                output=result,
                output_text=result_data["text"],
                output_message=result_data["message"],
                function=_sync_function,
                function_async=_async_function,
                function_name=result_data["function_call_info"]["name"],
                function_args=result_data["function_call_info"]["arguments"],
                function_arguments=_tool_arguments
            )
    else:
        return OutputWithFunctionCall(
                output=result,
                output_message=result_data["message"],
                output_text=result_data["text"],
            )

def log_results(result_data, result, is_function_call, verbose, prompt_type=None):
    if verbose or prompt_type:
        print_log(log_object=f"\nResult:\n{result}", log_level=prompt_type.log_level if verbose else 100,color=prompt_type.color if prompt_type else LogColors.BLUE)
        if is_function_call:
            function_call_info_str = json.dumps(result_data.get('function_call_info'),indent=4)
            print_log(log_object=f"\nFunction call:\n{function_call_info_str}", log_level=prompt_type.log_level if verbose else 100,color=prompt_type.color if prompt_type else LogColors.BLUE)