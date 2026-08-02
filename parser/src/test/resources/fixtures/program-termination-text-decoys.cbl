       IDENTIFICATION DIVISION.
       PROGRAM-ID. TERMTEXT.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-GOBACK-FLAG PIC X(1).
       PROCEDURE DIVISION.
       MAIN-PARA.
      * A comment mentioning STOP RUN and EXIT PROGRAM must never
      * influence structural classification.
           MOVE 'Y' TO WS-GOBACK-FLAG
           GOBACK.
