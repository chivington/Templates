#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <math.h>


// global variables & constants
char* program_title = "Awesome Program";


// formatting functions
void underline(char* msg, int indent, int before, int after) {
  indent ? (before ? printf("\n %s\n ", msg) : printf(" %s\n ", msg)) : (before ? printf("\n%s\n", msg) : printf("%s\n", msg));
  for (int i=0; i<strlen(msg); i++) printf("-");
  if (after) printf("\n");
};

void clear(void) {
  for (int i=0; i<1; i++) printf("\e[1;1H\e[2J");
};


// runtime utility functions
void greet(char* title) {
  clear();
  char msg[strlen("Welcome to ") + strlen(title)];
  strcat(msg, "Welcome to ");
  strcat(msg, title);
  underline(msg, 1, 1, 1);
};

char* prompt() {
  char* choice = malloc(10 * sizeof(char));
  printf("\n What would you like to do?\n >> ");
  scanf("%s", choice);
  return choice;
};


// help menu
void help(char* msg) {
  greet(msg);
  printf("\n Below are the commands available at each prompt.");
  printf("\n   help - display the help menu.");
  printf("\n   quit - exit the program.\n");
};


// main / driver
int main(int argc, char* argv) {
  greet(program_title);

  char* a = "Hello ";
  char* b = "World";
  char* c = "World";

  printf("\n same(1): %d", strcmp(a,b));
  printf("\n break");
  printf("\n same(2): %d\n", strcmp(b,c));

  // runtime
  // while (1) {
  //   char* choice;
  //   choice = prompt();
  //   printf("\n your choice: %s\n", choice);
  //   if (strcmp(choice, "quit")) break;
  //   if (strcmp(choice, "help")) help(program_title);
  //   // free(choice);
  // }

  return 0;
};
