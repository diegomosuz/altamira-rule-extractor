       IDENTIFICATION DIVISION.
       PROGRAM-ID. TERMKINDS.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-FLAG PIC X(1).
       PROCEDURE DIVISION.
       MAIN-PARA.
           IF WS-FLAG = 'A'
               GOBACK
           END-IF
           IF WS-FLAG = 'B'
               EXIT PROGRAM
           END-IF
           STOP RUN.
