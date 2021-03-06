import time
import joblib
import os
import os.path as osp
import tensorflow as tf
import torch
import numpy as np
from spinup import EpochLogger
from spinup.utils.logx import restore_tf_graph


def load_policy_and_env(fpath, itr='last', deterministic=False):
    """
    Load a policy from save, whether it's TF or PyTorch, along with RL env.

    Not exceptionally future-proof, but it will suffice for basic uses of the 
    Spinning Up implementations.

    Checks to see if there's a tf1_save folder. If yes, assumes the model
    is tensorflow and loads it that way. Otherwise, loads as if there's a 
    PyTorch save.
    """

    # determine if tf save or pytorch save
    if any(['tf1_save' in x for x in os.listdir(fpath)]):
        backend = 'tf1'
    else:
        backend = 'pytorch'

    # handle which epoch to load from
    if itr=='last':
        # check filenames for epoch (AKA iteration) numbers, find maximum value

        if backend == 'tf1':
            saves = [int(x[8:]) for x in os.listdir(fpath) if 'tf1_save' in x and len(x)>8]

        elif backend == 'pytorch':
            pytsave_path = osp.join(fpath, 'pyt_save')
            # Each file in this folder has naming convention 'modelXX.pt', where
            # 'XX' is either an integer or empty string. Empty string case
            # corresponds to len(x)==8, hence that case is excluded.
            saves = [int(x.split('.')[0][5:]) for x in os.listdir(pytsave_path) if len(x)>8 and 'model' in x]

        itr = '%d'%max(saves) if len(saves) > 0 else ''

    else:
        assert isinstance(itr, int), \
            "Bad value provided for itr (needs to be int or 'last')."
        itr = '%d'%itr

    # load the get_action function
    if backend == 'tf1':
        get_action = load_tf_policy(fpath, itr, deterministic)
    else:
        get_action = load_pytorch_policy(fpath, itr, deterministic)

    # try to load environment from save
    # (sometimes this will fail because the environment could not be pickled)
    try:
        state = joblib.load(osp.join(fpath, 'vars'+itr+'.pkl'))
        env = state['env']
    except:
        env = None

    return env, get_action


def load_tf_policy(fpath, itr, deterministic=False):
    """ Load a tensorflow policy saved with Spinning Up Logger."""

    fname = osp.join(fpath, 'tf1_save'+itr)
    print('\n\nLoading from %s.\n\n'%fname)

    # load the things!
    sess = tf.Session()
    model = restore_tf_graph(sess, fname)

    # get the correct op for executing actions
    if deterministic and 'mu' in model.keys():
        # 'deterministic' is only a valid option for SAC policies
        print('Using deterministic action op.')
        action_op = model['mu']
    else:
        print('Using default action op.')
        action_op = model['pi']

    # make function for producing an action given a single state
    get_action = lambda x : sess.run(action_op, feed_dict={model['x']: x[None,:]})[0]

    return get_action


def load_pytorch_policy(fpath, itr, deterministic=False):
    """ Load a pytorch policy saved with Spinning Up Logger."""
    
    fname = osp.join(fpath, 'pyt_save', 'model'+itr+'.pt')
    print('\n\nLoading from %s.\n\n'%fname)

    model = torch.load(fname)

    # make function for producing an action given a single state
    def get_action(x):
        with torch.no_grad():
            x = torch.as_tensor(x, dtype=torch.float32)
            action = model.act(x)
        return action

    return get_action


def run_policy(env, get_action, max_ep_len=None, num_episodes=100, render=True):

    assert env is not None, \
        "Environment not found!\n\n It looks like the environment wasn't saved, " + \
        "and we can't run the agent in it. :( \n\n Check out the readthedocs " + \
        "page on Experiment Outputs for how to handle this situation."

    logger = EpochLogger()
    o, r, d, ep_ret, ep_len, n = env.reset(), 0, False, 0, 0, 0
    while n < num_episodes:
        if render:
            env.render()
            time.sleep(1e-3)

        a = get_action(o)
        o, r, d, _ = env.step(a)
        ep_ret += r
        ep_len += 1

        if d or (ep_len == max_ep_len):
            logger.store(EpRet=ep_ret, EpLen=ep_len)
            print('Episode %d \t EpRet %.3f \t EpLen %d'%(n, ep_ret, ep_len))
            o, r, d, ep_ret, ep_len = env.reset(), 0, False, 0, 0
            n += 1

    logger.log_tabular('EpRet', with_min_and_max=True)
    logger.log_tabular('EpLen', average_only=True)
    logger.dump_tabular()


def run_pipeline(env, policy_dict, max_ep_len=None, num_episodes=100, render=True):

    assert env is not None, \
        "Environment not found!\n\n It looks like the environment wasn't saved, " + \
        "and we can't run the agent in it. :( \n\n Check out the readthedocs " + \
        "page on Experiment Outputs for how to handle this situation."

    logger = EpochLogger()
    o, r, d, ep_ret, ep_len, n = env.reset(), 0, False, 0, 0, 0
    phase = "reach"

    success = np.zeros(num_episodes, dtype=np.bool)

    while n < num_episodes:
        if render:
            env.render()
            time.sleep(1e-3)
        
        # ################################################
        # ##### model (action) pipeline defined here #####
        # ################################################
        #
        # Policy evaluation is stored in 'policy_dict' as values. Keys can just
        # be a convenient label or name.
        #
        
        # reach policy was trained on an observation state
        # of dimension 16 (since no object is used)
        #
        # The definition of o_subset isL
        #
        # [0:3] achieved goal
        # [3:6] desired goal
        # [6:16] observation (for FetchReach observation)
        # 
        # It comes in this order because of how FilterObservation works
        # (see https://github.com/openai/gym/blob/d993d5fd3553b6af051ba801c964fe7ebfc38ad1/gym/wrappers/filter_observation.py)
        # Line 28 in the link above outputs in this order.
        
        # The state 'o' is a FetchPickAndPlace observation, its
        # elements are defined here
        # https://github.com/openai/gym/blob/master/gym/envs/robotics/fetch_env.py
        # on line 112.

        # des_goal = o[28:] + [0,0,0.1]
        if phase == "reach":
            ahv_goal = o[0:3]
            des_goal = o[3:6] + [0,0,0.1]

            o_subset = np.concatenate((ahv_goal, des_goal, o[0:3], o[9:11], o[20:25]))
            a = policy_dict['reach'](o_subset)

            # force the gripper open with applying torque 1 
            a_ = [a[0],a[1],a[2],1]
            if np.linalg.norm(ahv_goal - des_goal, axis=-1) < 0.045:
                phase = "down"
        elif phase == "down":
            ahv_goal = o[0:3]
            des_goal = o[3:6] + [0.005,0,-0.0005]

            o_subset = np.concatenate((ahv_goal, des_goal, o[0:3], o[9:11], o[20:25]))
            a = policy_dict['reach'](o_subset)
            a_ = [a[0],a[1],a[2],0.25]
            if np.linalg.norm(ahv_goal - des_goal, axis=-1) < 0.045:
                phase = "pick"
                # cnt serves as a timer  
                cnt = 500
        elif phase == "pick":
            ahv_goal = o[0:3]
            des_goal = o[3:6] + [0.005,0,-0.0005]

            o_subset = np.concatenate((ahv_goal, des_goal, o[0:3], o[9:11], o[20:25]))
            a = policy_dict['reach'](o_subset)
            # force the gripper close with applying torque 1 
            a_ = [a[0],a[1],a[2],-1]
            cnt = cnt - 1
            if (np.linalg.norm(ahv_goal - des_goal, axis=-1) < 0.03) & (cnt > 0):
                phase = "place"
        elif phase == "place":
            ahv_goal = o[0:3]
            des_goal = o[28:]

            o_subset = np.concatenate((ahv_goal, des_goal, o[0:3], o[9:11], o[20:25]))
            a = policy_dict['reach'](o_subset)
            a_ = [a[0],a[1],a[2],-1]
            if np.linalg.norm(ahv_goal - des_goal, axis=-1) < 0.03:
                phase = "place"

        
        # ################################################

        o, r, d, _ = env.step(a_)
        ep_ret += r
        ep_len += 1

        if np.linalg.norm(o[25:28] - o[28:31], ord=2) < 0.02:
            success[n] = True

        if d or (ep_len == max_ep_len):
            logger.store(EpRet=ep_ret, EpLen=ep_len)
            print('Episode %d \t EpRet %.3f \t EpLen %d'%(n, ep_ret, ep_len))

            success_rate = np.sum(success[:n+1]) / (n+1)
            print('Episode %d \t SuccessRate %.3f'%(n, success_rate))

            o, r, d, ep_ret, ep_len = env.reset(), 0, False, 0, 0
            phase = "reach"
            n += 1

    logger.log_tabular('EpRet', with_min_and_max=True)
    logger.log_tabular('EpLen', average_only=True)
    logger.dump_tabular()



if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('fpath', type=str)
    parser.add_argument('--len', '-l', type=int, default=0)
    parser.add_argument('--episodes', '-n', type=int, default=100)
    parser.add_argument('--norender', '-nr', action='store_true')
    parser.add_argument('--itr', '-i', type=int, default=-1)
    parser.add_argument('--deterministic', '-d', action='store_true')
    args = parser.parse_args()
    env, get_action = load_policy_and_env(args.fpath, 
                                          args.itr if args.itr >=0 else 'last',
                                          args.deterministic)
    run_policy(env, get_action, args.len, args.episodes, not(args.norender))
