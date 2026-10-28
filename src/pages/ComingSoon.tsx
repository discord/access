import React from 'react';
import Grid from '@mui/material/Grid';
import Paper from '@mui/material/Paper';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';

export default function ComingSoon() {
  return (
    <React.Fragment>
      <Paper>
        <Grid container spacing={2} sx={{padding: 2}}>
          <Grid size={12}>
            <Grid
              container
              sx={{
                justifyContent: 'center',
              }}>
              <Grid>
                <Box
                  component="img"
                  src="/logo.png"
                  alt="Access logo"
                  sx={{
                    width: 350,
                  }}
                />
              </Grid>
            </Grid>
          </Grid>
          <Grid size={12}>
            <Grid
              container
              sx={{
                justifyContent: 'center',
              }}>
              <Grid>
                <Typography
                  component="h2"
                  variant="h3"
                  sx={{
                    color: 'text.accent',
                  }}>
                  Coming Soon!
                </Typography>
              </Grid>
            </Grid>
          </Grid>
        </Grid>
      </Paper>
    </React.Fragment>
  );
}
