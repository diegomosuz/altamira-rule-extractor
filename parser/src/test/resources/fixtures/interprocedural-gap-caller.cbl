       IDENTIFICATION DIVISION.
       PROGRAM-ID. GAPCALLER.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-A PIC X(10).
       01 WS-B PIC X(10).
       01 WS-C PIC X(10).
       01 WS-FLAG PIC X(1).
       PROCEDURE DIVISION.
       MAIN-PARA.
           CALL 'GAPCALLEE' USING BY VALUE WS-A
           CALL 'GAPCALLEE' USING WS-A WS-B WS-C
           CALL 'GAPCALLEE' USING WS-A
           IF WS-FLAG = 'Y'
               CALL 'GAPCALLEE' USING WS-A WS-B
           END-IF
           EVALUATE WS-FLAG
               WHEN 'Y'
                   CALL 'GAPCALLEE' USING WS-A WS-B
               WHEN OTHER
                   CONTINUE
           END-EVALUATE
           CALL 'GAPCALLEE'
               ON EXCEPTION
                   CONTINUE
           END-CALL
           CALL 'GAPCALLEE'
               NOT ON EXCEPTION
                   CONTINUE
           END-CALL
           CALL 'GAPCALLER' USING WS-A
           STOP RUN.
