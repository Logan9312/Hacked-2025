import os
import dotenv
import json
from typing import Annotated
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import ToolMessage

#from langchain.agents import create_react_agent
from langchain_openai import ChatOpenAI
from langchain_community.tools.tavily_search import TavilySearchResults
from langgraph.checkpoint.memory import MemorySaver

from .pm_tools import fetch_pm_document_url
# Load environment variables
dotenv.load_dotenv()
OPENAI_KEY = os.getenv("OPENAI_API_KEY")


class State(TypedDict):
    messages: Annotated[list, add_messages]


class BasicToolNode:
    """A node that runs the tools requested in the last AIMessage."""

    def __init__(self, tools: list) -> None:
        self.tools_by_name = {tool.name: tool for tool in tools}

    def __call__(self, inputs: dict):
        if messages := inputs.get("messages", []):
            message = messages[-1]
        else:
            raise ValueError("No message found in input")
        outputs = []
        for tool_call in message.tool_calls:
            tool_result = self.tools_by_name[tool_call["name"]].invoke(
                tool_call["args"]
            )
            outputs.append(
                ToolMessage(
                    content=json.dumps(tool_result),
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                )
            )
        return {"messages": outputs}


class LLM:
    def __init__(self):
        self.graph_builder = StateGraph(State)

        #tool = TavilySearchResults(max_results=2)
        self.tools = [fetch_pm_document_url]
        self.llm = ChatOpenAI(model="gpt-4o-mini")
        self.llm_with_tools = self.llm.bind_tools(self.tools)

        self.graph_builder.add_node("chatbot", self.chatbot)
        self.graph_builder.add_edge(START, "chatbot")

        self.tool_node = BasicToolNode(tools=[fetch_pm_document_url])
        self.graph_builder.add_node("tools", self.tool_node)
        self.graph_builder.add_conditional_edges(
            "chatbot", self.route_tools, {"tools": "tools", END: END}
        )
        self.graph_builder.add_edge("tools", "chatbot")
        self.mem_saver = MemorySaver()
        self.graph = self.graph_builder.compile(checkpointer=self.mem_saver)
        self.mem_saver_config = {"configurable": {"thread_id": "def234"}}
        
        
    def chatbot(self, state: State) -> State:
        return {"messages": [self.llm_with_tools.invoke(state["messages"])]}

    def stream_graph_updates(self, user_input: str):
        for event in self.graph.stream(
            {
                "messages": [
                    {"role": "system", "content": "You are a heathcare search engine assistant, whose purpose is to extract the keyword from the user's question about healthcare. Do not attempt to answer the question. If the question is about any other topic that is not about health, output \'Please only ask question about health.\'. If the question is about health, output the keywords in the question, with AND in between each keyword."},
                    {"role": "user", "content": user_input}
                ]
            },
            config=self.mem_saver_config,
        ):
            
            for value in event.values():
                #print(value["messages"][-1].content)
                yield value["messages"][-1].content

    def route_tools(self, state: State):
        if isinstance(state, list):
            ai_message = state[-1]
        elif messages := state.get("messages", []):
            ai_message = messages[-1]
        else:
            raise ValueError(f"No messages found in input state to tool_edge: {state}")
        if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
            return "tools"
        return END


user_input = "What do you know about LangGraph?"
test = LLM()
test.stream_graph_updates(user_input)