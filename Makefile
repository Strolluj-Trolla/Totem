all: Compile_server Compile_client
	echo "Project compiled."
Compile_server:
	echo "Compiling the server..."
	g++ -I ./include/ -g -Wall -Wextra server.cpp -o server
Compile_client:
	echo "Checking for errors in client..."
	python3 -m py_compile client.py
	rm ./__pycache__/client.cpython*