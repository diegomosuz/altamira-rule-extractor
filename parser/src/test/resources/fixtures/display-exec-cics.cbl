       IDENTIFICATION DIVISION.
       PROGRAM-ID. DISPCICS.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-MSG                   PIC X(30) VALUE 'MENSAJE DE PRUEBA'.
       01 WS-MSG-LEN               PIC S9(4) COMP VALUE 30.
       PROCEDURE DIVISION.
       MAIN-PARA.
           DISPLAY WS-MSG.
           EXEC CICS
               SEND TEXT
               FROM (WS-MSG)
               LENGTH (WS-MSG-LEN)
           END-EXEC.
           GOBACK.
