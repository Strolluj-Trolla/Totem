#include <netdb.h>
#include <cstdio>
#include <arpa/inet.h>
#include <errno.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/epoll.h>
#include <thread>
#include <vector>
#include <iostream>

#define buff_size 50

struct client{
    int fd;
    std::string nick;
};
struct room{
    long id;
    client* players[8];
    time_t joinTimes[8];
    int spectatorCount;
};
typedef struct client client;
typedef struct room room;

void handleComms(int clientSocket, sockaddr_in clientAddress){
    char buff[buff_size]="Siemka\000";
    printf("Connection from %s\n", inet_ntoa(clientAddress.sin_addr));
    write(clientSocket, buff, buff_size);
    shutdown(clientSocket, SHUT_RDWR);
    close(clientSocket);
    return;
}

int main(int argc, char** argv){
    if(argc<2){
        printf("Enter port number as an argument.\n");
        return 10;
    }

    addrinfo hints {.ai_flags=AI_PASSIVE, .ai_family=AF_INET, .ai_protocol = IPPROTO_TCP};
    addrinfo * resolved;
    int res;
    if((res= getaddrinfo("localhost", argv[1], &hints, &resolved))) {fprintf(stderr, "Getaddrinfo failed: %s\n", gai_strerror(res)); return 1;}

    int sock;
    if((sock=socket(resolved->ai_family, resolved->ai_socktype, resolved->ai_protocol))==-1){
        perror("Unable to create a socket.\n");
        return errno;
    };
    const int one = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    bind(sock, resolved->ai_addr, resolved->ai_addrlen);
    listen(sock, 5);
    fcntl(sock, F_SETFL, (fcntl(sock, F_GETFL)|O_NONBLOCK));

    std::vector<client> clients;
    std::vector<room> rooms;

    while(true){
        sockaddr_in clientAddr;
        socklen_t clientAddrLen=sizeof(clientAddr);
        int clientSock=accept(sock, (sockaddr*)&clientAddr, &clientAddrLen);
        if(clientSock!=-1){
            client cl={.fd=clientSock};
            clients.push_back(cl);
            std::thread(handleComms, clientSock, clientAddr).detach();
        }
        else{
            sleep(1);
            printf("Sleeping\n");
        }
    }

    freeaddrinfo(resolved);
    shutdown(sock, SHUT_RDWR);
    close(sock);

    return 0;

}